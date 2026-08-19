"""Strict timezone conversion shared by validation and planning."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import PlanningError


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {value}") from exc
    return value


def parse_activity_time(value: str, timezone: str, field: str) -> datetime:
    """Parse an ISO timestamp and reject ambiguous/nonexistent local times."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PlanningError(f"{field} is not a valid ISO 8601 datetime: {value}") from exc
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(UTC)

    zone = ZoneInfo(timezone)
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = parsed.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip == parsed:
            candidates.append(candidate)
    if not candidates:
        raise PlanningError(
            f"{field} does not exist in {timezone}; choose a valid local time"
        )
    if len({candidate.utcoffset() for candidate in candidates}) > 1:
        raise PlanningError(
            f"{field} is ambiguous in {timezone}; include an explicit UTC offset"
        )
    return candidates[0].astimezone(UTC)


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def format_local_naive(value: datetime, timezone: str) -> str:
    return value.astimezone(ZoneInfo(timezone)).replace(tzinfo=None).isoformat()

