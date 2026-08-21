"""Process-wide logging configuration for the Web host."""

from __future__ import annotations

import json
import logging
from typing import Any

from capture_node.core.events import EVENT_ATTRIBUTE


class JsonEventFormatter(logging.Formatter):
    """Formats Web host log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, EVENT_ATTRIBUTE, None)
        if event is not None:
            return json.dumps(event, ensure_ascii=True, default=str)
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(level: str = "INFO") -> None:
    """Install the Web host's console logging policy."""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonEventFormatter())
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        handlers=[handler],
        force=True,
    )
