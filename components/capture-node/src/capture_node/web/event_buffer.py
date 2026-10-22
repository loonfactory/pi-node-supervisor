"""In-memory Web projection of Core log events."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

from capture_node.core.events import EVENT_ATTRIBUTE


class EventBuffer(logging.Handler):
    """Retains recent structured events for the current process lifetime."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        if capacity <= 0:
            raise ValueError("event capacity must be positive")
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._events_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        event = getattr(record, EVENT_ATTRIBUTE, None)
        if not isinstance(event, dict):
            return
        with self._events_lock:
            self._events.appendleft(dict(event))

    def snapshot(self) -> list[dict[str, Any]]:
        with self._events_lock:
            return list(self._events)

    def latest_capture_id(self) -> str | None:
        with self._events_lock:
            for event in self._events:
                if event.get("eventType") == "capture.succeeded":
                    capture_id = event.get("captureId")
                    return capture_id if isinstance(capture_id, str) else None
        return None

    def page(
        self,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 50:
            raise ValueError("page_size must be between 1 and 50")
        with self._events_lock:
            total = len(self._events)
            start = (page - 1) * page_size
            return list(self._events)[start : start + page_size], total
