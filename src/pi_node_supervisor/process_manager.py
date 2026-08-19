"""Supervisor-owned lifecycle for local CaptureNode Web child processes."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .adapters import CaptureNodeClient
from .errors import SupervisorError
from .models import CaptureScheduleSettings, NodeDefinition, StorageSettingsDocument
from .registry import NodeRegistry
from .runtime_store import RuntimeStore


class ChildProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


ProcessFactory = Callable[[list[str], Path | None], ChildProcess]
HealthCheck = Callable[[NodeDefinition], dict[str, Any]]


class CaptureProcessError(SupervisorError):
    """A managed CaptureNode process could not be started or recovered."""


def _spawn(arguments: list[str], working_directory: Path | None) -> ChildProcess:
    return subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=working_directory,
    )


def _health(node: NodeDefinition) -> dict[str, Any]:
    assert node.endpoint is not None
    client = CaptureNodeClient(
        node.endpoint,
        request_timeout_seconds=min(node.request_timeout_seconds, 2),
        completion_timeout_seconds=node.completion_timeout_seconds,
    )
    return client.health()


class CaptureNodeProcessManager:
    """Starts, monitors, restarts, and drains configured CaptureNode children."""

    def __init__(
        self,
        registry: NodeRegistry,
        executable: Path,
        runtime: RuntimeStore,
        *,
        process_factory: ProcessFactory = _spawn,
        health_check: HealthCheck = _health,
    ) -> None:
        self.nodes = tuple(node for node in registry if node.managed_capture is not None)
        self.executable = executable
        self.runtime = runtime
        self.process_factory = process_factory
        self.health_check = health_check
        self._processes: dict[str, ChildProcess] = {}
        self._log_threads: dict[str, threading.Thread] = {}
        self._restart_counts: dict[str, int] = {node.id: 0 for node in self.nodes}
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return bool(self.nodes)

    def start(self) -> None:
        with self._lifecycle_lock:
            self._start()

    def _start(self) -> None:
        if not self.nodes:
            return
        if self._monitor is not None:
            raise CaptureProcessError("CaptureNode process manager is already running")
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise CaptureProcessError(
                f"CaptureNode executable is unavailable: {self.executable}"
            )
        for node in self.nodes:
            assert node.managed_capture is not None
            if not node.managed_capture.config_path.is_file():
                raise CaptureProcessError(
                    f"CaptureNode config is unavailable for {node.id}: "
                    f"{node.managed_capture.config_path}"
                )

        self._stop.clear()
        try:
            for node in self.nodes:
                self._start_node(node, event_type="node.process.started")
            for node in self.nodes:
                self._wait_ready(node)
        except BaseException:
            self._terminate_all()
            raise

        self._monitor = threading.Thread(
            target=self._monitor_loop,
            name="pi-node-supervisor-capture-processes",
            daemon=False,
        )
        self._monitor.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_manager()

    def _stop_manager(self) -> None:
        self._stop.set()
        monitor = self._monitor
        if monitor is not None:
            monitor.join()
            self._monitor = None
        self._terminate_all()

    def camera_configuration(self, node_id: str) -> dict[str, Any]:
        node = self._managed_node(node_id)
        assert node.managed_capture is not None
        document = self._read_configuration(node.managed_capture.config_path)
        return {
            "node_id": node.id,
            "camera": document.get("camera"),
            "capture": document.get("capture"),
            "image": document.get("image"),
            "storages": document.get("storages", []),
            "maintenance": document.get("maintenance"),
        }

    def update_storages(
        self, node_id: str, settings: StorageSettingsDocument
    ) -> dict[str, Any]:
        with self._lifecycle_lock:
            node = self._managed_node(node_id)
            assert node.managed_capture is not None
            path = node.managed_capture.config_path
            previous_bytes = path.read_bytes()
            previous = self._read_configuration(path)
            candidate = dict(previous)
            candidate["storages"] = [
                item.model_dump(mode="json") for item in settings.items
            ]
            names = {item.name for item in settings.items}
            maintenance = candidate.get("maintenance")
            removed_retention_targets: list[str] = []
            if isinstance(maintenance, dict):
                retention = maintenance.get("retention", [])
                if isinstance(retention, list):
                    retained = []
                    for rule in retention:
                        target = rule.get("target") if isinstance(rule, dict) else None
                        if target in names:
                            retained.append(rule)
                        elif isinstance(target, str):
                            removed_retention_targets.append(target)
                    candidate["maintenance"] = {**maintenance, "retention": retained}

            restarted_nodes = self._apply_configuration_update(
                updates=[(node, path, previous_bytes, candidate)],
                event_node_id=node.id,
                update_name="storage",
                success_event="node.config.storages_updated",
                event_payload={
                    "storage_names": sorted(names),
                    "removed_retention_targets": removed_retention_targets,
                },
            )
            return {
                "node_id": node.id,
                "storages": candidate["storages"],
                "removed_retention_targets": removed_retention_targets,
                "restarted_nodes": restarted_nodes,
            }

    def update_all_storages(
        self, settings: StorageSettingsDocument
    ) -> dict[str, Any]:
        with self._lifecycle_lock:
            if not self.nodes:
                raise ValueError("there are no managed CaptureNodes")
            serialized = [item.model_dump(mode="json") for item in settings.items]
            names = {item.name for item in settings.items}
            updates: list[tuple[NodeDefinition, Path, bytes, dict[str, Any]]] = []
            removed_by_node: dict[str, list[str]] = {}
            for node in self.nodes:
                assert node.managed_capture is not None
                path = node.managed_capture.config_path
                previous_bytes = path.read_bytes()
                previous = self._read_configuration(path)
                node_storages = [
                    {
                        **item,
                        **(
                            {"directory": item["directory"].replace("{camera}", node.id)}
                            if isinstance(item.get("directory"), str)
                            else {}
                        ),
                        **(
                            {"remote_directory": item["remote_directory"].replace("{camera}", node.id)}
                            if isinstance(item.get("remote_directory"), str)
                            else {}
                        ),
                    }
                    for item in serialized
                ]
                candidate = {**previous, "storages": node_storages}
                removed: list[str] = []
                maintenance = candidate.get("maintenance")
                if isinstance(maintenance, dict):
                    retention = maintenance.get("retention", [])
                    if isinstance(retention, list):
                        retained = []
                        for rule in retention:
                            target = rule.get("target") if isinstance(rule, dict) else None
                            if target in names:
                                retained.append(rule)
                            elif isinstance(target, str):
                                removed.append(target)
                        candidate["maintenance"] = {**maintenance, "retention": retained}
                removed_by_node[node.id] = removed
                updates.append((node, path, previous_bytes, candidate))

            restarted_nodes = self._apply_configuration_update(
                updates=updates,
                event_node_id=None,
                update_name="common storage",
                success_event="node.config.common_storages_updated",
                event_payload={
                    "storage_names": sorted(names),
                    "removed_retention_targets": removed_by_node,
                    "node_ids": [node.id for node in self.nodes],
                },
            )
            return {
                "scope": "common",
                "storages": serialized,
                "removed_retention_targets": removed_by_node,
                "restarted_nodes": restarted_nodes,
            }

    def apply_native_schedules(
        self,
        settings_by_node: dict[str, CaptureScheduleSettings],
        activate: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply CaptureNode timers and their Supervisor power revision together."""

        with self._lifecycle_lock:
            selected = [self._managed_node(node_id) for node_id in settings_by_node]
            updates: list[tuple[NodeDefinition, Path, bytes, dict[str, Any]]] = []
            for node in selected:
                assert node.managed_capture is not None
                path = node.managed_capture.config_path
                previous_bytes = path.read_bytes()
                previous = self._read_configuration(path)
                capture = previous.get("capture")
                if not isinstance(capture, dict):
                    raise CaptureProcessError(
                        f"CaptureNode configuration has no capture section for {node.id}"
                    )
                schedule = settings_by_node[node.id].model_dump(mode="json")
                if all(capture.get(key) == value for key, value in schedule.items()):
                    continue
                candidate = {
                    **previous,
                    "capture": {
                        **capture,
                        **schedule,
                    },
                }
                updates.append((node, path, previous_bytes, candidate))
            activation_result: dict[str, Any] = {}

            def activate_revision() -> None:
                nonlocal activation_result
                activation_result = activate()

            if not updates:
                activate_revision()
                return {
                    "updated_nodes": [],
                    "restarted_nodes": [],
                    "revision": activation_result,
                }
            restarted_nodes = self._apply_configuration_update(
                updates=updates,
                event_node_id=None if len(selected) > 1 else selected[0].id,
                update_name="capture and power schedule",
                success_event="node.config.capture_power_schedules_updated",
                event_payload={
                    "node_ids": [node.id for node, *_rest in updates],
                    "modes": {
                        node.id: settings_by_node[node.id].mode
                        for node, *_rest in updates
                    },
                },
                after_start=activate_revision,
            )
            return {
                "updated_nodes": [node.id for node, *_rest in updates],
                "restarted_nodes": restarted_nodes,
                "revision": activation_result,
            }

    def _apply_configuration_update(
        self,
        *,
        updates: list[tuple[NodeDefinition, Path, bytes, dict[str, Any]]],
        event_node_id: str | None,
        update_name: str,
        success_event: str,
        event_payload: dict[str, Any],
        after_start: Callable[[], None] | None = None,
    ) -> list[str]:
        target = event_node_id or "all managed CaptureNodes"
        self._stop_manager()
        try:
            for _node, path, _previous_bytes, candidate in updates:
                self._write_configuration(path, candidate)
            self._start()
            if after_start is not None:
                after_start()
        except BaseException as apply_error:
            try:
                if self._monitor is not None:
                    self._stop_manager()
                for _node, path, previous_bytes, _candidate in updates:
                    self._write_bytes(path, previous_bytes)
                self._start()
            except BaseException as rollback_error:
                self.runtime.append_event(
                    "node.config.rollback_failed",
                    severity="error",
                    node_id=event_node_id,
                    payload={
                        "update": update_name,
                        "apply_error": f"{type(apply_error).__name__}: {apply_error}",
                        "rollback_error": f"{type(rollback_error).__name__}: {rollback_error}",
                    },
                )
                raise CaptureProcessError(
                    f"{update_name} update and rollback failed for {target}: {rollback_error}"
                ) from rollback_error
            self.runtime.append_event(
                "node.config.update_failed",
                severity="error",
                node_id=event_node_id,
                payload={
                    "update": update_name,
                    "error": f"{type(apply_error).__name__}: {apply_error}",
                },
            )
            raise CaptureProcessError(
                f"{update_name} update was rejected for {target}; previous configuration restored"
            ) from apply_error

        self.runtime.append_event(
            success_event,
            node_id=event_node_id,
            payload=event_payload,
        )
        return [item.id for item in self.nodes]

    def statuses(self) -> list[dict[str, Any]]:
        with self._lock:
            processes = dict(self._processes)
            restart_counts = dict(self._restart_counts)
        items: list[dict[str, Any]] = []
        for node in self.nodes:
            process = processes.get(node.id)
            exit_code = process.poll() if process is not None else None
            items.append(
                {
                    "node_id": node.id,
                    "managed": True,
                    "pid": process.pid if process is not None and exit_code is None else None,
                    "running": process is not None and exit_code is None,
                    "restart_count": restart_counts[node.id],
                    "exit_code": exit_code,
                }
            )
        return items

    def _managed_node(self, node_id: str) -> NodeDefinition:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise ValueError(f"node is not a managed CaptureNode: {node_id}")

    @staticmethod
    def _read_configuration(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CaptureProcessError(f"cannot read CaptureNode config {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise CaptureProcessError("CaptureNode configuration root must be an object")
        return value

    @classmethod
    def _write_configuration(cls, path: Path, document: dict[str, Any]) -> None:
        cls._write_bytes(
            path,
            (json.dumps(document, ensure_ascii=True, indent=2) + "\n").encode("utf-8"),
        )

    @staticmethod
    def _write_bytes(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def _arguments(self, node: NodeDefinition) -> list[str]:
        managed = node.managed_capture
        assert managed is not None
        return [
            str(self.executable),
            "--config",
            str(managed.config_path),
            "--host",
            managed.host,
            "--port",
            str(managed.port),
            "--log-level",
            "INFO",
        ]

    def _start_node(self, node: NodeDefinition, *, event_type: str) -> None:
        assert node.managed_capture is not None
        process = self.process_factory(
            self._arguments(node), node.managed_capture.working_directory
        )
        with self._lock:
            self._processes[node.id] = process
        output = getattr(process, "stdout", None)
        if output is not None:
            reader = threading.Thread(
                target=self._forward_logs,
                args=(node.id, output),
                name=f"pi-node-supervisor-{node.id}-logs",
                daemon=True,
            )
            self._log_threads[node.id] = reader
            reader.start()
        self.runtime.append_event(
            event_type,
            node_id=node.id,
            payload={
                "pid": process.pid,
                "restart_count": self._restart_counts[node.id],
            },
        )

    def _wait_ready(self, node: NodeDefinition) -> None:
        managed = node.managed_capture
        assert managed is not None
        deadline = time.monotonic() + managed.startup_timeout_seconds
        last_error = "health endpoint is not ready"
        while not self._stop.is_set():
            with self._lock:
                process = self._processes[node.id]
            exit_code = process.poll()
            if exit_code is not None:
                raise CaptureProcessError(
                    f"CaptureNode {node.id} exited during startup with code {exit_code}"
                )
            try:
                self.health_check(node)
                self.runtime.append_event(
                    "node.process.ready",
                    node_id=node.id,
                    payload={"pid": process.pid},
                )
                return
            except Exception as exc:
                last_error = str(exc)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CaptureProcessError(
                    f"CaptureNode {node.id} did not become ready: {last_error}"
                )
            self._stop.wait(min(0.25, remaining))
        raise CaptureProcessError(f"CaptureNode {node.id} startup was cancelled")

    def _monitor_loop(self) -> None:
        while not self._stop.wait(0.25):
            for node in self.nodes:
                with self._lock:
                    process = self._processes[node.id]
                exit_code = process.poll()
                if exit_code is None:
                    continue
                self.runtime.append_event(
                    "node.process.exited",
                    severity="error",
                    node_id=node.id,
                    payload={"pid": process.pid, "exit_code": exit_code},
                )
                managed = node.managed_capture
                assert managed is not None
                if self._stop.wait(managed.restart_delay_seconds):
                    return
                self._restart_counts[node.id] += 1
                try:
                    self._start_node(node, event_type="node.process.restarted")
                    self._wait_ready(node)
                except Exception as exc:
                    with self._lock:
                        failed_process = self._processes[node.id]
                    if failed_process.poll() is None:
                        failed_process.terminate()
                        try:
                            failed_process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            failed_process.kill()
                            failed_process.wait(timeout=5)
                    self.runtime.append_event(
                        "node.process.restart_failed",
                        severity="error",
                        node_id=node.id,
                        payload={
                            "restart_count": self._restart_counts[node.id],
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )

    def _terminate_all(self) -> None:
        with self._lock:
            processes = list(self._processes.items())
            self._processes.clear()
        for node_id, process in processes:
            if process.poll() is None:
                process.terminate()
        for node_id, process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self.runtime.append_event(
                "node.process.stopped",
                node_id=node_id,
                payload={"pid": process.pid, "exit_code": process.poll()},
            )
        readers = list(self._log_threads.values())
        self._log_threads.clear()
        for reader in readers:
            reader.join(timeout=2)

    def _forward_logs(self, node_id: str, output: Any) -> None:
        """Persist actionable CaptureNode output as Supervisor events."""

        for raw_line in output:
            line = raw_line.strip()
            if not line:
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                document = {"message": line}
            if not isinstance(document, dict):
                document = {"message": line}

            source_event_type = document.get("event_type")
            if isinstance(source_event_type, str):
                severity = self._capture_event_severity(source_event_type)
                event_type = f"capture-node.{source_event_type}"
            else:
                severity = self._capture_log_severity(document)
                if severity is None:
                    continue
                event_type = "capture-node.log"
            try:
                self.runtime.append_event(
                    event_type,
                    severity=severity,
                    node_id=node_id,
                    payload={"source": "capture-node", **document},
                )
            except Exception:
                # Logging must never terminate or restart a healthy capture process.
                continue

    @staticmethod
    def _capture_log_severity(document: dict[str, Any]) -> str | None:
        """Discard routine stdout while retaining unstructured warnings and errors."""

        message = str(document.get("message", ""))
        upper_message = message.upper()
        if any(
            marker in message
            for marker in (
                "Unsupported V4L2 pixel format",
                "No static properties available",
                "Please consider updating the camera sensor properties database",
                "No sensor delays found in static properties",
            )
        ):
            return None
        level = str(document.get("level", "INFO")).upper()
        if level in {"ERROR", "CRITICAL", "FATAL"} or any(
            marker in upper_message for marker in (" ERROR ", " CRITICAL ", " FATAL ")
        ):
            return "error"
        if level in {"WARNING", "WARN"} or any(
            marker in upper_message for marker in (" WARNING ", " WARN ")
        ):
            return "warning"
        return None

    @staticmethod
    def _capture_event_severity(event_type: str) -> str:
        if event_type in {
            "capture.failed",
            "terminal.discarded",
            "maintenance.retention_failed",
        }:
            return "error"
        if event_type in {"capture.skipped", "storage.no_targets"}:
            return "warning"
        return "info"
