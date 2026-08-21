from __future__ import annotations

from datetime import UTC, datetime

from .errors import ValidationError


def ensure_utc(value: datetime, *, field: str = "time") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field} must include a UTC offset")
    return value.astimezone(UTC)


def parse_datetime(value: str, *, field: str = "time") -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid ISO 8601 datetime: {value}") from exc
    return ensure_utc(parsed, field=field)


def format_datetime(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")

