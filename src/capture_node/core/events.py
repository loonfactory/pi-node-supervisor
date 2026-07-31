"""Stable structured events emitted by the core application."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

LOGGER_NAME = "capture_node"
EVENT_ATTRIBUTE = "capture_event"
SCHEMA_VERSION = 1

logger = logging.getLogger(LOGGER_NAME)
logger.addHandler(logging.NullHandler())


class EventType(StrEnum):
    STARTED = "lifecycle.started"
    STOPPED = "lifecycle.stopped"
    NO_STORAGE_TARGETS = "storage.no_targets"
    CAPTURE_SUCCEEDED = "capture.succeeded"
    CAPTURE_FAILED = "capture.failed"
    CAPTURE_SKIPPED = "capture.skipped"
    TERMINAL_COMPLETED = "terminal.completed"
    TERMINAL_DISCARDED = "terminal.discarded"
    CONFIG_CHANGED = "config.changed"


LEVELS: Mapping[EventType, int] = {
    EventType.STARTED: logging.INFO,
    EventType.STOPPED: logging.INFO,
    EventType.NO_STORAGE_TARGETS: logging.WARNING,
    EventType.CAPTURE_SUCCEEDED: logging.INFO,
    EventType.CAPTURE_FAILED: logging.ERROR,
    EventType.CAPTURE_SKIPPED: logging.WARNING,
    EventType.TERMINAL_COMPLETED: logging.INFO,
    EventType.TERMINAL_DISCARDED: logging.ERROR,
    EventType.CONFIG_CHANGED: logging.INFO,
}


def cause_payload(cause: BaseException) -> dict[str, Any]:
    return {
        "code": type(cause).__name__,
        "message": str(cause),
    }


class EventEmitter:
    """Central structured-event entry point for Core."""

    def __init__(self, target: logging.Logger | None = None) -> None:
        self._logger = target or logger

    def emit(self, event_type: EventType, **fields: Any) -> dict[str, Any]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "event_type": event_type.value,
            "timestamp": datetime.now(UTC).isoformat(),
            **fields,
        }
        self._logger.log(
            LEVELS[event_type],
            event_type.value,
            extra={EVENT_ATTRIBUTE: payload},
        )
        return payload
