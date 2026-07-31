from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from PIL import Image

from capture_node.core.cameras import CameraStatus, Capture
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
