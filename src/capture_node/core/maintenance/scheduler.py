"""Independent fixed-delay scheduler for maintenance work."""

from __future__ import annotations

import threading
from collections.abc import Callable


class MaintenanceScheduler:
    def __init__(self, interval_seconds: float, tick: Callable[[], None]) -> None:
        if interval_seconds <= 0:
            raise ValueError("maintenance interval must be positive")
        self._interval_seconds = interval_seconds
        self._tick = tick
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("maintenance scheduler is already started")
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run,
                name="capture-node-maintenance",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._condition:
            thread = self._thread
            if thread is None:
                return
            self._stopping = True
            self._condition.notify_all()
        thread.join()
        with self._condition:
            self._thread = None

    def _run(self) -> None:
        while True:
            self._tick()
            with self._condition:
                if self._condition.wait_for(
                    lambda: self._stopping,
                    timeout=self._interval_seconds,
                ):
                    return
