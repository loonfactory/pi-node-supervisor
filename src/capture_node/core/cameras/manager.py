"""Thread-safe implementation used by the configured camera provider."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

from capture_node.core.cameras.contracts import (
    Camera,
    CameraControl,
    CameraControlSource,
    CameraStatus,
    CameraStatusSource,
    Capture,
    FrameSource,
    PreviewFrame,
)


class CameraManager:
    """Serializes camera use and swaps implementations without changing Core."""

    def __init__(self, camera: Camera) -> None:
        self._camera = camera
        self._generation = 0
        self._lock = threading.RLock()
        self._closed = False

    def capture(self) -> Capture:
        with self._lock:
            self._ensure_open()
            return self._camera.capture()

    def apply(self, parameters: Mapping[str, Any]) -> None:
        with self._lock:
            self._ensure_open()
            self._camera.apply(parameters)

    def controls(self) -> Sequence[CameraControl]:
        with self._lock:
            self._ensure_open()
            if not isinstance(self._camera, CameraControlSource):
                return ()
            return tuple(self._camera.controls())

    def status(self) -> CameraStatus:
        with self._lock:
            if self._closed:
                return CameraStatus(False, "camera manager is closed")
            if isinstance(self._camera, CameraStatusSource):
                return self._camera.status()
            return CameraStatus(True)

    def frames(self) -> Iterator[PreviewFrame]:
        generation = -1
        frames: Iterator[PreviewFrame] | None = None
        while True:
            with self._lock:
                self._ensure_open()
                if generation != self._generation:
                    if not isinstance(self._camera, FrameSource):
                        raise RuntimeError(
                            "the active camera does not provide preview frames"
                        )
                    generation = self._generation
                    frames = self._camera.frames()
                assert frames is not None
                frame = next(frames)
            yield frame

    def replace(
        self,
        camera_factory: Callable[[], Camera],
        rollback_factory: Callable[[], Camera],
    ) -> None:
        with self._lock:
            self._ensure_open()
            previous = self._camera
            self._close_camera(previous)
            try:
                replacement = camera_factory()
            except BaseException as replacement_error:
                try:
                    self._camera = rollback_factory()
                except BaseException as rollback_error:
                    self._closed = True
                    raise RuntimeError(
                        "camera replacement and rollback both failed"
                    ) from rollback_error
                self._generation += 1
                raise replacement_error
            self._camera = replacement
            self._generation += 1

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._close_camera(self._camera)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("camera manager is closed")

    @staticmethod
    def _close_camera(camera: Camera) -> None:
        close = getattr(camera, "close", None)
        if close is not None:
            close()
