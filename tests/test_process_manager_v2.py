from __future__ import annotations

import json
from pathlib import Path

from pi_node_supervisor.process_manager_v2 import CaptureNodeProcessManagerV2


class Process:
    next_pid = 100

    def __init__(self, arguments):
        self.arguments = arguments
        self.pid = Process.next_pid
        Process.next_pid += 1
        self.code = None

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15

    def kill(self):
        self.code = -9

    def wait(self, timeout=None):
        if self.code is None:
            self.code = 0
        return self.code


def test_manager_generates_one_config_and_process_per_camera(tmp_path: Path) -> None:
    executable = tmp_path / "capture-node-web"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    processes = []

    def spawn(arguments, _working_directory):
        process = Process(arguments)
        processes.append(process)
        return process

    manager = CaptureNodeProcessManagerV2(
        executable,
        tmp_path / "generated",
        {
            "rgb": {
                "camera": {"type": "picamera2", "cameraNum": 0},
                "images": {"dng": {"embedMetadata": True}},
                "captures": [],
                "storages": [],
            },
            "noir": {
                "camera": {"type": "picamera2", "cameraNum": 1},
                "images": {"dng": {"embedMetadata": True}},
            },
        },
        process_factory=spawn,
        health_check=lambda _spec: None,
    )

    manager.start()

    assert len(processes) == 2
    assert manager.endpoints == {
        "noir": "http://127.0.0.1:8000",
        "rgb": "http://127.0.0.1:8001",
    }
    rgb = json.loads((tmp_path / "generated/rgb.json").read_text())
    assert "captures" not in rgb and "storages" not in rgb
    assert processes[1].arguments[1:3] == [
        "--config",
        str(tmp_path / "generated/rgb.json"),
    ]
    manager.stop()
