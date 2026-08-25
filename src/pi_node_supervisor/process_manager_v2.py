"""Supervisor-owned camera process lifecycle using generated v2 configs."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .errors import SupervisorError


class ChildProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


class CaptureProcessErrorV2(SupervisorError):
    pass


@dataclass(frozen=True)
class CaptureProcessSpec:
    device_id: str
    config_path: Path
    endpoint: str
    host: str
    port: int


ProcessFactory = Callable[[list[str], Path | None], ChildProcess]
HealthCheck = Callable[[CaptureProcessSpec], None]


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


class CaptureNodeProcessManagerV2:
    def __init__(
        self,
        executable: Path,
        config_directory: Path,
        camera_configs: dict[str, dict[str, Any]],
        *,
        host: str = "127.0.0.1",
        first_port: int = 8000,
        startup_timeout_seconds: float = 30,
        restart_delay_seconds: float = 2,
        working_directory: Path | None = None,
        process_factory: ProcessFactory = _spawn,
        health_check: HealthCheck | None = None,
    ) -> None:
        self.executable = executable
        self.config_directory = config_directory
        self.camera_configs = {
            name: _generated_document(document)
            for name, document in camera_configs.items()
        }
        self.startup_timeout_seconds = startup_timeout_seconds
        self.restart_delay_seconds = restart_delay_seconds
        self.working_directory = working_directory
        self.process_factory = process_factory
        self.health_check = health_check or (lambda _spec: None)
        self.specs = {
            device_id: CaptureProcessSpec(
                device_id=device_id,
                config_path=config_directory / f"{device_id}.json",
                endpoint=f"http://{host}:{first_port + index}",
                host=host,
                port=first_port + index,
            )
            for index, device_id in enumerate(sorted(camera_configs))
        }
        self._processes: dict[str, ChildProcess] = {}
        self._restart_counts = {device_id: 0 for device_id in self.specs}
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def endpoints(self) -> dict[str, str]:
        return {name: spec.endpoint for name, spec in self.specs.items()}

    def write_configs(self) -> None:
        self.config_directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        for device_id, document in self.camera_configs.items():
            _atomic_json(self.specs[device_id].config_path, document)

    def start(self) -> None:
        with self._lock:
            if self._monitor is not None:
                raise CaptureProcessErrorV2("CaptureNode process manager is already running")
            if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
                raise CaptureProcessErrorV2(
                    f"CaptureNode executable is unavailable: {self.executable}"
                )
            self.write_configs()
            self._stop.clear()
            try:
                for spec in self.specs.values():
                    self._start_process(spec)
                for spec in self.specs.values():
                    self._wait_ready(spec)
            except BaseException:
                self._terminate_all()
                raise
            self._monitor = threading.Thread(
                target=self._monitor_loop,
                name="pi-node-supervisor-capture-nodes-v2",
                daemon=False,
            )
            self._monitor.start()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            monitor = self._monitor
        if monitor is not None:
            monitor.join(timeout=max(5, self.restart_delay_seconds + 1))
        with self._lock:
            self._monitor = None
            self._terminate_all()

    def _start_process(self, spec: CaptureProcessSpec) -> None:
        process = self.process_factory(self._arguments(spec), self.working_directory)
        self._processes[spec.device_id] = process

    def _arguments(self, spec: CaptureProcessSpec) -> list[str]:
        return [
            str(self.executable),
            "--config",
            str(spec.config_path),
            "--host",
            spec.host,
            "--port",
            str(spec.port),
            "--log-level",
            "INFO",
        ]

    def _wait_ready(self, spec: CaptureProcessSpec) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            process = self._processes[spec.device_id]
            code = process.poll()
            if code is not None:
                raise CaptureProcessErrorV2(
                    f"CaptureNode {spec.device_id} exited during startup with code {code}"
                )
            try:
                self.health_check(spec)
                return
            except Exception as exc:
                last_error = exc
                self._stop.wait(0.1)
        raise CaptureProcessErrorV2(
            f"CaptureNode {spec.device_id} did not become ready: {last_error or 'timeout'}"
        )

    def _monitor_loop(self) -> None:
        while not self._stop.wait(0.25):
            for device_id, spec in self.specs.items():
                with self._lock:
                    process = self._processes.get(device_id)
                    if process is None or process.poll() is None:
                        continue
                if self._stop.wait(self.restart_delay_seconds):
                    return
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._restart_counts[device_id] += 1
                    try:
                        self._start_process(spec)
                        self._wait_ready(spec)
                    except Exception:
                        continue

    def _terminate_all(self) -> None:
        processes = list(self._processes.values())
        self._processes.clear()
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _generated_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if key not in {"captures", "storages"}
    }


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    content = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
