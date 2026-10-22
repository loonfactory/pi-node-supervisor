"""Select a live sensor request without blocking the Picamera2 event loop."""

from __future__ import annotations

import threading
from typing import Any


class SynchronizedFrames:
    """Transfer one acquired request from the camera callback to the caller."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._target: int | None = None
        self._request: Any = None
        self._closed = False

    def observe(self, request: Any) -> None:
        metadata = request.get_metadata()
        with self._condition:
            if (
                self._closed
                or self._target is None
                or self._request is not None
                or not metadata.get("SyncReady", False)
                or metadata.get("SensorTimestamp", 0) < self._target
            ):
                return
            request.acquire()
            self._request = request
            self._condition.notify_all()

    def capture(self, target_ns: int, timeout: float) -> Any:
        with self._condition:
            if self._closed:
                raise RuntimeError("camera is closed")
            if self._target is not None:
                raise RuntimeError("a synchronized capture is already pending")
            self._target = target_ns
            try:
                ready = self._condition.wait_for(
                    lambda: self._request is not None or self._closed, timeout
                )
                if self._closed:
                    raise RuntimeError("camera is closed")
                if not ready:
                    raise TimeoutError("no synchronized sensor frame arrived before timeout")
                request, self._request = self._request, None
                return request
            finally:
                self._target = None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            if self._request is not None:
                self._request.release()
                self._request = None
            self._condition.notify_all()
