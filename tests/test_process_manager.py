from __future__ import annotations

import json
from io import StringIO
import time
from pathlib import Path

import pytest

from pi_node_supervisor.models import (
    CaptureNodeSettings,
    CaptureScheduleSettings,
    NodeRegistryDocument,
    StorageSettingsDocument,
)
from pi_node_supervisor.process_manager import CaptureNodeProcessManager, CaptureProcessError
from pi_node_supervisor.registry import NodeRegistry
from pi_node_supervisor.runtime_store import RuntimeStore


class FakeProcess:
    next_pid = 100

    def __init__(self, arguments: list[str]) -> None:
        self.arguments = arguments
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.code: int | None = None

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.code = -15

    def kill(self) -> None:
        self.code = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.code is None:
            self.code = 0
        return self.code


def managed_registry(config_path: Path) -> NodeRegistry:
    return NodeRegistry(
        NodeRegistryDocument.model_validate(
            {
                "schema_version": 1,
                "nodes": [
                    {
                        "id": "camera0",
                        "kind": "capture-node",
                        "activity_types": ["capture"],
                        "profiles": ["default"],
                        "endpoint": "http://127.0.0.1:8000",
                        "managed_capture": {
                            "config_path": str(config_path),
                            "working_directory": str(config_path.parent),
                            "host": "127.0.0.1",
                            "port": 8000,
                            "startup_timeout_seconds": 1,
                            "restart_delay_seconds": 0.1,
                        },
                    }
                ],
            }
        )
    )


def dual_managed_registry(first: Path, second: Path) -> NodeRegistry:
    nodes = []
    for index, path in enumerate((first, second)):
        nodes.append(
            {
                "id": f"camera{index}",
                "kind": "capture-node",
                "activity_types": ["capture"],
                "profiles": ["default"],
                "endpoint": f"http://127.0.0.1:{8000 + index}",
                "managed_capture": {
                    "config_path": str(path),
                    "working_directory": str(path.parent),
                    "host": "127.0.0.1",
                    "port": 8000 + index,
                    "startup_timeout_seconds": 1,
                    "restart_delay_seconds": 0.1,
                },
            }
        )
    return NodeRegistry(
        NodeRegistryDocument.model_validate(
            {"schema_version": 1, "nodes": nodes}
        )
    )


def test_managed_process_is_started_recovered_and_stopped(tmp_path: Path):
    executable = tmp_path / "capture-node-web"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    config = tmp_path / "camera.json"
    config.write_text("{}", encoding="utf-8")
    processes: list[FakeProcess] = []

    working_directories: list[Path | None] = []

    def spawn(arguments: list[str], working_directory: Path | None) -> FakeProcess:
        working_directories.append(working_directory)
        process = FakeProcess(arguments)
        processes.append(process)
        return process

    runtime = RuntimeStore(tmp_path / "state.db")
    manager = CaptureNodeProcessManager(
        managed_registry(config),
        executable,
        runtime,
        process_factory=spawn,
        health_check=lambda _node: {"status": "running", "camera_connected": True},
    )
    manager.start()
    assert processes[0].arguments == [
        str(executable),
        "--config",
        str(config),
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--log-level",
        "INFO",
    ]
    assert working_directories == [config.parent]
    processes[0].code = 7
    deadline = time.monotonic() + 2
    while len(processes) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(processes) == 2
    assert manager.statuses()[0]["restart_count"] == 1

    manager.stop()
    assert processes[1].code == -15
    event_types = [event.event_type for event in runtime.list_events(limit=100)]
    assert "node.process.exited" in event_types
    assert "node.process.restarted" in event_types
    assert "node.process.stopped" in event_types


def test_missing_trusted_executable_fails_before_start(tmp_path: Path):
    config = tmp_path / "camera.json"
    config.write_text("{}", encoding="utf-8")
    manager = CaptureNodeProcessManager(
        managed_registry(config),
        tmp_path / "missing",
        RuntimeStore(tmp_path / "state.db"),
    )
    with pytest.raises(CaptureProcessError, match="executable is unavailable"):
        manager.start()


def test_storage_update_restarts_children_and_removes_orphan_retention(tmp_path: Path):
    executable = tmp_path / "capture-node-web"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    config = tmp_path / "camera.json"
    config.write_text(
        json.dumps(
            {
                "camera": {"backend": "test"},
                "storages": [
                    {"type": "file", "name": "local", "directory": "old"},
                    {"type": "file", "name": "archive", "directory": "archive"},
                ],
                "maintenance": {
                    "retention": [
                        {"target": "local", "max_age_days": 7},
                        {"target": "archive", "max_age_days": 30},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    processes: list[FakeProcess] = []

    def spawn(arguments: list[str], _working_directory: Path | None) -> FakeProcess:
        process = FakeProcess(arguments)
        processes.append(process)
        return process

    runtime = RuntimeStore(tmp_path / "state.db")
    manager = CaptureNodeProcessManager(
        managed_registry(config),
        executable,
        runtime,
        process_factory=spawn,
        health_check=lambda _node: {"status": "running"},
    )
    manager.start()
    result = manager.update_storages(
        "camera0",
        StorageSettingsDocument.model_validate(
            {"items": [{"type": "file", "name": "local", "directory": "captures", "layout": "daily"}]}
        ),
    )

    updated = json.loads(config.read_text(encoding="utf-8"))
    assert updated["storages"] == [
        {"type": "file", "name": "local", "directory": "captures", "layout": "daily"}
    ]
    assert updated["maintenance"]["retention"] == [
        {"target": "local", "max_age_days": 7}
    ]
    assert result["removed_retention_targets"] == ["archive"]
    assert len(processes) == 2
    assert processes[0].code == -15
    assert manager.camera_configuration("camera0")["storages"] == updated["storages"]
    manager.stop()


def test_storage_update_can_create_retention_configuration(tmp_path: Path):
    executable = tmp_path / "capture-node-web"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    config = tmp_path / "camera.json"
    config.write_text(
        json.dumps(
            {
                "camera": {"backend": "test"},
                "storages": [
                    {"type": "file", "name": "local", "directory": "old"}
                ],
            }
        ),
        encoding="utf-8",
    )
    manager = CaptureNodeProcessManager(
        managed_registry(config),
        executable,
        RuntimeStore(tmp_path / "state.db"),
        process_factory=lambda arguments, _cwd: FakeProcess(arguments),
        health_check=lambda _node: {"status": "running"},
    )
    manager.start()

    manager.update_storages(
        "camera0",
        StorageSettingsDocument.model_validate(
            {
                "items": [
                    {
                        "type": "file",
                        "name": "local",
                        "directory": "captures/camera0",
                    }
                ],
                "retention": [{"target": "local", "max_age_days": 7}],
            }
        ),
    )

    updated = json.loads(config.read_text(encoding="utf-8"))
    assert updated["maintenance"] == {
        "interval_seconds": 86400,
        "retention": [{"target": "local", "max_age_days": 7}],
    }
    manager.stop()


def test_failed_storage_update_restores_previous_configuration(tmp_path: Path):
    executable = tmp_path / "capture-node-web"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    config = tmp_path / "camera.json"
    config.write_text(
        '{"storages":[{"type":"file","name":"local","directory":"captures"}]}\n',
        encoding="utf-8",
    )
    original = config.read_bytes()
    processes: list[FakeProcess] = []

    def spawn(arguments: list[str], _working_directory: Path | None) -> FakeProcess:
        process = FakeProcess(arguments)
        document = json.loads(config.read_text(encoding="utf-8"))
        if document["storages"][0]["directory"] == "bad":
            process.code = 2
        processes.append(process)
        return process

    runtime = RuntimeStore(tmp_path / "state.db")
    manager = CaptureNodeProcessManager(
        managed_registry(config),
        executable,
        runtime,
        process_factory=spawn,
        health_check=lambda _node: {"status": "running"},
    )
    manager.start()

    with pytest.raises(CaptureProcessError, match="previous configuration restored"):
        manager.update_storages(
            "camera0",
            StorageSettingsDocument.model_validate(
                {"items": [{"type": "file", "name": "local", "directory": "bad"}]}
            ),
        )

    assert config.read_bytes() == original
    assert len(processes) == 3
    assert manager.statuses()[0]["running"] is True
    assert "node.config.update_failed" in [
        event.event_type for event in runtime.list_events(limit=100)
    ]
    manager.stop()


def test_native_schedule_and_power_revision_apply_together(tmp_path: Path):
    executable = tmp_path / "capture-node-web"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    config = tmp_path / "camera.json"
    config.write_text(
        json.dumps(
            {
                "capture": {
                    "mode": "interval",
                    "interval_seconds": 2.5,
                    "daily_times": [],
                    "timezone": "Asia/Seoul",
                    "params": {"ExposureTime": 12000},
                },
                "storages": [],
            }
        ),
        encoding="utf-8",
    )
    processes: list[FakeProcess] = []

    def spawn(arguments: list[str], _working_directory: Path | None) -> FakeProcess:
        process = FakeProcess(arguments)
        processes.append(process)
        return process

    runtime = RuntimeStore(tmp_path / "state.db")
    manager = CaptureNodeProcessManager(
        managed_registry(config),
        executable,
        runtime,
        process_factory=spawn,
        health_check=lambda _node: {"status": "running"},
    )
    manager.start()
    result = manager.apply_native_schedules(
        {
            "camera0": CaptureScheduleSettings.model_validate(
                {
                    "mode": "daily",
                    "interval_seconds": None,
                    "daily_times": ["08:30:00", "17:45:30"],
                    "timezone": "Asia/Seoul",
                }
            )
        },
        lambda: {"id": 2, "status": "active"},
    )

    updated = json.loads(config.read_text(encoding="utf-8"))["capture"]
    assert updated == {
        "mode": "daily",
        "interval_seconds": None,
        "daily_times": ["08:30:00", "17:45:30"],
        "timezone": "Asia/Seoul",
        "params": {"ExposureTime": 12000},
    }
    assert result["revision"] == {"id": 2, "status": "active"}
    assert len(processes) == 2
    assert "node.config.capture_power_schedules_updated" in [
        event.event_type for event in runtime.list_events(limit=100)
    ]
    manager.stop()


def test_capture_node_structured_logs_are_persisted(tmp_path: Path):
    executable = tmp_path / "capture-node-web"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    config = tmp_path / "camera.json"
    config.write_text("{}", encoding="utf-8")
    runtime = RuntimeStore(tmp_path / "state.db")
    manager = CaptureNodeProcessManager(managed_registry(config), executable, runtime)

    manager._forward_logs(
        "camera0",
        StringIO(
            '{"schema_version":1,"event_type":"capture.succeeded",'
            '"timestamp":"2030-01-01T00:00:00Z","capture_id":"cap-1"}\n'
            'INFO:     127.0.0.1:12345 - "GET /api/health HTTP/1.1" 200 OK\n'
            '{"level":"INFO","logger":"picamera2.picamera2","message":"Camera started"}\n'
            '[1.0] WARN V4L2 v4l2_pixelformat.cpp: Unsupported V4L2 pixel format Nc12\n'
            '[1.1] ERROR V4L2 v4l2_device.cpp: Device or resource busy\n'
            '{"level":"ERROR","logger":"capture_node.web","message":"camera lost"}\n'
        ),
    )

    events = runtime.list_events(limit=10)
    assert [event.event_type for event in events] == [
        "capture-node.log",
        "capture-node.log",
        "capture-node.capture.succeeded",
    ]
    assert events[0].severity == "error"
    assert events[1].severity == "error"
    assert events[2].payload["capture_id"] == "cap-1"


def test_common_storage_expands_camera_template_for_all_nodes(tmp_path: Path):
    executable = tmp_path / "capture-node-web"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    configs = [tmp_path / "camera0.json", tmp_path / "camera1.json"]
    for config in configs:
        config.write_text('{"capture":{"mode":"disabled"},"storages":[]}\n', encoding="utf-8")
    processes: list[FakeProcess] = []

    def spawn(arguments: list[str], _working_directory: Path | None) -> FakeProcess:
        process = FakeProcess(arguments)
        processes.append(process)
        return process

    manager = CaptureNodeProcessManager(
        dual_managed_registry(*configs),
        executable,
        RuntimeStore(tmp_path / "state.db"),
        process_factory=spawn,
        health_check=lambda _node: {"status": "running"},
    )
    manager.start()
    result = manager.update_all_storages(
        StorageSettingsDocument.model_validate(
            {
                "items": [
                    {
                        "type": "file",
                        "name": "local",
                        "directory": "captures/{camera}",
                        "layout": "daily",
                    }
                ]
            }
        )
    )

    assert [
        json.loads(config.read_text(encoding="utf-8"))["storages"][0]["directory"]
        for config in configs
    ] == ["captures/camera0", "captures/camera1"]
    assert result["scope"] == "common"
    assert result["restarted_nodes"] == ["camera0", "camera1"]
    assert len(processes) == 4
    manager.stop()


def test_capture_node_settings_merge_common_and_node_overlays(tmp_path: Path):
    executable = tmp_path / "capture-node-web"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    configs = [tmp_path / "camera0.json", tmp_path / "camera1.json"]
    for index, path in enumerate(configs):
        path.write_text(
            json.dumps(
                {
                    "camera": {"camera_num": index},
                    "capture": {"params": {"gain": index}},
                }
            ),
            encoding="utf-8",
        )
    processes: list[FakeProcess] = []

    def spawn(arguments: list[str], _working_directory: Path | None) -> FakeProcess:
        process = FakeProcess(arguments)
        processes.append(process)
        return process

    manager = CaptureNodeProcessManager(
        dual_managed_registry(*configs),
        executable,
        RuntimeStore(tmp_path / "state.db"),
        process_factory=spawn,
        health_check=lambda _node: {"status": "running"},
    )
    manager.start()
    result = manager.apply_settings_overlays(
        CaptureNodeSettings.model_validate(
            {
                "common": {
                    "capture": {
                        "params": {
                            "horizontal_flip": True,
                            "vertical_flip": True,
                        }
                    },
                    "storages": [
                        {
                            "type": "file",
                            "name": "local",
                            "directory": "captures/{camera}",
                            "layout": "daily",
                        }
                    ],
                },
                "nodes": {
                    "camera1": {"capture": {"params": {"vertical_flip": False}}}
                },
            }
        ),
        lambda: {"id": 9},
    )

    first = json.loads(configs[0].read_text(encoding="utf-8"))
    second = json.loads(configs[1].read_text(encoding="utf-8"))
    assert first["capture"]["params"] == {
        "gain": 0,
        "horizontal_flip": True,
        "vertical_flip": True,
    }
    assert second["capture"]["params"] == {
        "gain": 1,
        "horizontal_flip": True,
        "vertical_flip": False,
    }
    assert first["storages"][0]["directory"] == "captures/camera0"
    assert second["storages"][0]["directory"] == "captures/camera1"
    assert result["updated_nodes"] == ["camera0", "camera1"]
    assert result["revision"] == {"id": 9}
    manager.stop()
