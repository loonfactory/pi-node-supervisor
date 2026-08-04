from __future__ import annotations

import threading
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from PIL import Image

from capture_node.core.cameras import CameraStatus, Capture, PreviewFrame
from capture_node.core.cameras.manager import CameraManager


class ManagedCamera:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False
        self.parameters = {}

    def capture(self) -> Capture:
        if self.closed:
            raise RuntimeError("closed")
        return Capture(Image.new("RGB", (2, 2)), datetime.now(UTC), {"name": self.name})

    def apply(self, parameters: Mapping) -> None:
        self.parameters = dict(parameters)

    def close(self) -> None:
        self.closed = True


class StatusCamera(ManagedCamera):
    def status(self) -> CameraStatus:
        return CameraStatus(False, "USB camera disconnected")


class BroadcastCamera(ManagedCamera):
    def __init__(self) -> None:
        super().__init__("broadcast")
        self._condition = threading.Condition()
        self._waiting = 0
        self._sequence = 0

    def frames(self):
        sequence = 0
        with self._condition:
            self._waiting += 1
            self._condition.notify_all()
            self._condition.wait_for(lambda: self._sequence > sequence)
            sequence = self._sequence
        yield PreviewFrame(str(sequence).encode("ascii"))

    def wait_for_consumers(self, count: int, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self._waiting >= count, timeout)

    def publish(self) -> None:
        with self._condition:
            self._sequence += 1
            self._condition.notify_all()


def test_camera_manager_replaces_and_closes_previous_camera() -> None:
    first = ManagedCamera("first")
    second = ManagedCamera("second")
    manager = CameraManager(first)

    manager.replace(lambda: second, lambda: ManagedCamera("rollback"))

    assert first.closed
    assert manager.capture().parameters["name"] == "second"
    manager.close()
    assert second.closed


def test_camera_manager_reopens_previous_configuration_on_failure() -> None:
    first = ManagedCamera("first")
    rollback = ManagedCamera("rollback")
    manager = CameraManager(first)

    with pytest.raises(RuntimeError, match="new device failed"):
        manager.replace(
            lambda: (_ for _ in ()).throw(RuntimeError("new device failed")),
            lambda: rollback,
        )

    assert first.closed
    assert manager.capture().parameters["name"] == "rollback"


def test_camera_manager_exposes_implementation_status() -> None:
    manager = CameraManager(StatusCamera("offline"))

    status = manager.status()

    assert not status.connected
    assert status.detail == "USB camera disconnected"


def test_camera_manager_does_not_serialize_preview_consumers() -> None:
    camera = BroadcastCamera()
    manager = CameraManager(camera)
    received: list[bytes] = []

    def consume() -> None:
        received.append(next(manager.frames()).data)

    consumers = [threading.Thread(target=consume) for _ in range(2)]
    for consumer in consumers:
        consumer.start()

    assert camera.wait_for_consumers(2, timeout=1)
    camera.publish()

    for consumer in consumers:
        consumer.join(timeout=1)
        assert not consumer.is_alive()
    assert received == [b"1", b"1"]
