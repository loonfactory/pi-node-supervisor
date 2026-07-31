"""Core camera placeholder used when hardware is unavailable."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from capture_node.core.cameras.contracts import CameraStatus, Capture


class UnavailableCamera:
    """Rejects capture while exposing the camera startup failure."""

    def __init__(self, cause: BaseException) -> None:
        self._detail = str(cause) or type(cause).__name__

    def capture(self) -> Capture:
        raise RuntimeError(f"camera is unavailable: {self._detail}")

    def apply(self, parameters: Mapping[str, Any]) -> None:
        return

    def status(self) -> CameraStatus:
        return CameraStatus(False, self._detail)
