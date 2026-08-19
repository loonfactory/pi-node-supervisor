from __future__ import annotations

import socket
import threading
import time
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

capture_node = pytest.importorskip("capture_node")
uvicorn = pytest.importorskip("uvicorn")

from PIL import Image
from capture_node.core import CaptureNode
from capture_node.core.cameras import CameraStatus, Capture
from capture_node.core.config import Configuration, JsonOptionsRepository
from capture_node.core.events import LOGGER_NAME
from capture_node.core.options import Options
from capture_node.core.options import CaptureOptions
from capture_node.core.storages import ConfiguredStorageProvider, FileStorageOptions
from capture_node.web.server import create_app

from pi_node_supervisor.adapters import CaptureNodeClient
from pi_node_supervisor.models import CaptureScheduleSettings


class Camera:
    def __init__(self) -> None:
        self.calls = 0

    def capture(self) -> Capture:
        self.calls += 1
        return Capture(
            Image.new("RGB", (16, 12), (32, 144, 210)),
            datetime.now(UTC),
            {"Frame": self.calls},
        )

    def apply(self, _parameters) -> None:
        return None

    def status(self) -> CameraStatus:
        return CameraStatus(connected=True)


class CameraProvider:
    def __init__(self) -> None:
        self.value = Camera()

    def camera(self) -> Camera:
        return self.value

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    "document",
    [
        {
            "mode": "interval",
            "interval_seconds": 2.5,
            "daily_times": [],
            "timezone": "Asia/Seoul",
        },
        {
            "mode": "daily",
            "interval_seconds": None,
            "daily_times": ["08:30:00", "17:45:30"],
            "timezone": "Asia/Seoul",
        },
        {
            "mode": "disabled",
            "interval_seconds": None,
            "daily_times": [],
            "timezone": "UTC",
        },
    ],
)
def test_supervisor_schedule_is_accepted_by_capture_node(document):
    schedule = CaptureScheduleSettings.model_validate(document)
    accepted = CaptureOptions.model_validate(schedule.model_dump(mode="json"))
    assert accepted.mode == document["mode"]


def configuration(path: Path, captures: Path) -> Configuration:
    options = Options.model_validate(
        {
            "capture": {
                "mode": "disabled",
                "interval_seconds": None,
                "daily_times": [],
                "timezone": "UTC",
                "params": {},
            },
            "image": {
                "format": "jpeg",
                "quality": 90,
                "embed_metadata": True,
            },
            "storages": [
                {
                    "type": "file",
                    "name": "local",
                    "directory": str(captures),
                    "layout": "flat",
                }
            ],
        }
    )
    repository = JsonOptionsRepository(path)
    repository.write(options)
    return Configuration(repository, options)


def test_real_capture_node_http_contract_is_consumed_end_to_end(tmp_path: Path):
    captures = tmp_path / "captures"
    provider = CameraProvider()
    storage = ConfiguredStorageProvider(
        (FileStorageOptions(type="file", name="local", directory=captures, layout="flat"),)
    )
    node = CaptureNode(provider, configuration(tmp_path / "capture.json", captures), storage)
    logging.getLogger(LOGGER_NAME).setLevel(logging.INFO)
    app = create_app(node, provider, storage, manage_lifecycle=False)

    try:
        listener = socket.socket()
    except PermissionError:
        pytest.skip("the execution sandbox does not permit loopback sockets")
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", lifespan="on", access_log=False)
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        if not thread.is_alive() or time.monotonic() >= deadline:
            raise AssertionError("CaptureNode test server did not start")
        time.sleep(0.01)

    try:
        client = CaptureNodeClient(
            f"http://127.0.0.1:{port}",
            completion_timeout_seconds=5,
        )
        assert client.health()["camera_connected"] is True
        configured = client.configure_capture({"ExposureTime": 12000})
        assert configured["capture"]["params"] == {"ExposureTime": 12000}
        capture = client.capture()
        terminal = client.wait_for_terminal(capture["capture_id"])
        records = client.data_records(
            node_id="camera0",
            activity_id="morning-capture",
            run_id="run-1",
            capture_id=capture["capture_id"],
        )

        assert terminal["event_type"] == "terminal.completed"
        assert terminal["targets"] == ["local"]
        assert len(records) == 1
        assert records[0].run_id == "run-1"
        assert records[0].activity_id == "morning-capture"
        assert records[0].uri.endswith(records[0].data_id.split(":", 2)[2])
        assert list(captures.glob("*.jpg"))
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        node.stop()
