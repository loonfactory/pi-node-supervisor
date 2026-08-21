from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ValidationError
from .timeutil import format_datetime, parse_datetime

CONFIG_SCHEMA_VERSION = 2
STATE_SCHEMA_VERSION = 1


def _strict_keys(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError(f"unknown {context} keys: {', '.join(sorted(unknown))}")


def _schema_version(data: dict[str, Any], context: str) -> int:
    if "schema_version" not in data:
        raise ValidationError(f"{context} requires schema_version")
    value = data["schema_version"]
    if type(value) is not int:
        raise ValidationError(f"{context} schema_version must be an integer")
    return value


def _integer(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if type(value) is not int:
        raise ValidationError(f"{key} must be an integer")
    return value


def _boolean(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if type(value) is not bool:
        raise ValidationError(f"{key} must be a boolean")
    return value


@dataclass(frozen=True, slots=True)
class OneShotSchedule:
    id: str
    shutdown_at: datetime
    wake_at: datetime
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValidationError("schedule id must not be empty")
        if self.shutdown_at.tzinfo is None or self.wake_at.tzinfo is None:
            raise ValidationError("schedule times must include a UTC offset")
        object.__setattr__(self, "shutdown_at", self.shutdown_at.astimezone(UTC))
        object.__setattr__(self, "wake_at", self.wake_at.astimezone(UTC))
        if self.wake_at <= self.shutdown_at:
            raise ValidationError("wake_at must be later than shutdown_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "one_shot",
            "shutdown_at": format_datetime(self.shutdown_at),
            "wake_at": format_datetime(self.wake_at),
            "enabled": self.enabled,
        }


@dataclass(frozen=True, slots=True)
class PowerWindow:
    power_on: datetime
    power_down: datetime

    def __post_init__(self) -> None:
        if self.power_on.tzinfo is None or self.power_down.tzinfo is None:
            raise ValidationError("power window times must include a UTC offset")
        object.__setattr__(self, "power_on", self.power_on.astimezone(UTC))
        object.__setattr__(self, "power_down", self.power_down.astimezone(UTC))
        if self.power_down <= self.power_on:
            raise ValidationError("power_down must be later than power_on")

    @property
    def id(self) -> str:
        return f"power-down:{format_datetime(self.power_down)}"

    def to_dict(self, timezone: str) -> dict[str, str]:
        zone = ZoneInfo(timezone)
        return {
            "power-on": self.power_on.astimezone(zone).replace(tzinfo=None).isoformat(),
            "power-down": self.power_down.astimezone(zone).replace(tzinfo=None).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], timezone: str) -> "PowerWindow":
        _strict_keys(data, {"power-on", "power-down"}, "power_schedule item")
        for key in ("power-on", "power-down"):
            if not isinstance(data.get(key), str):
                raise ValidationError(f"{key} must be an ISO 8601 string")
        return cls(
            power_on=_parse_power_time(data["power-on"], timezone, "power-on"),
            power_down=_parse_power_time(data["power-down"], timezone, "power-down"),
        )


def _parse_power_time(value: str, timezone: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid ISO 8601 datetime: {value}") from exc
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(UTC)
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(f"unknown IANA timezone: {timezone}") from exc
    candidates = []
    for fold in (0, 1):
        candidate = parsed.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip == parsed:
            candidates.append(candidate)
    if not candidates:
        raise ValidationError(
            f"{field} does not exist in {timezone}; provide a valid local time: {value}"
        )
    offsets = {candidate.utcoffset() for candidate in candidates}
    if len(offsets) > 1:
        raise ValidationError(
            f"{field} is ambiguous in {timezone}; provide an explicit UTC offset: {value}"
        )
    return candidates[0].astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Configuration:
    schema_version: int = CONFIG_SCHEMA_VERSION
    timezone: str = "UTC"
    minimum_lead_seconds: int = 60
    clock_tolerance_seconds: int = 5
    power_schedule: tuple[PowerWindow, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != CONFIG_SCHEMA_VERSION:
            raise ValidationError(f"unsupported configuration schema version: {self.schema_version}")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError(f"unknown IANA timezone: {self.timezone}") from exc
        if self.minimum_lead_seconds < 1:
            raise ValidationError("minimum_lead_seconds must be at least 1")
        if self.clock_tolerance_seconds < 0:
            raise ValidationError("clock_tolerance_seconds must not be negative")
        ordered = tuple(sorted(self.power_schedule, key=lambda window: window.power_on))
        if ordered != self.power_schedule:
            raise ValidationError("power_schedule must be ordered by power_on")
        for current, following in zip(self.power_schedule, self.power_schedule[1:]):
            if following.power_on <= current.power_down:
                raise ValidationError(
                    "power_schedule items must not overlap or touch; "
                    f"{format_datetime(following.power_on)} is not after "
                    f"{format_datetime(current.power_down)}"
                )

    @property
    def schedules(self) -> tuple[OneShotSchedule, ...]:
        return tuple(
            OneShotSchedule(
                id=current.id,
                shutdown_at=current.power_down,
                wake_at=following.power_on,
            )
            for current, following in zip(self.power_schedule, self.power_schedule[1:])
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "timezone": self.timezone,
            "minimum_lead_seconds": self.minimum_lead_seconds,
            "clock_tolerance_seconds": self.clock_tolerance_seconds,
            "power_schedule": [
                window.to_dict(self.timezone) for window in self.power_schedule
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Configuration":
        _strict_keys(
            data,
            {
                "schema_version", "timezone", "minimum_lead_seconds",
                "clock_tolerance_seconds", "power_schedule",
            },
            "configuration",
        )
        timezone = data.get("timezone")
        if not isinstance(timezone, str) or not timezone:
            raise ValidationError("configuration requires an IANA timezone string")
        raw_schedule = data.get("power_schedule", [])
        if not isinstance(raw_schedule, list):
            raise ValidationError("power_schedule must be a list")
        if not all(isinstance(item, dict) for item in raw_schedule):
            raise ValidationError("each power_schedule item must be an object")
        return cls(
            schema_version=_schema_version(data, "configuration"),
            timezone=timezone,
            minimum_lead_seconds=_integer(data, "minimum_lead_seconds", 60),
            clock_tolerance_seconds=_integer(data, "clock_tolerance_seconds", 5),
            power_schedule=tuple(
                PowerWindow.from_dict(item, timezone) for item in raw_schedule
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeState:
    schema_version: int = STATE_SCHEMA_VERSION
    automatic_shutdown_enabled: bool = True
    suspended: bool = False
    suspended_until: datetime | None = None
    owned_alarm_epoch: int | None = None
    owned_schedule_id: str | None = None
    completed_schedule_ids: tuple[str, ...] = ()
    last_success: dict[str, Any] | None = None
    last_error: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.schema_version != STATE_SCHEMA_VERSION:
            raise ValidationError(f"unsupported state schema version: {self.schema_version}")
        if self.suspended_until is not None and self.suspended_until.tzinfo is None:
            raise ValidationError("suspended_until must include a UTC offset")
        if self.suspended_until is not None:
            object.__setattr__(self, "suspended_until", self.suspended_until.astimezone(UTC))

    def is_suspended(self, now: datetime) -> bool:
        if not self.suspended:
            return False
        return self.suspended_until is None or now < self.suspended_until

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["completed_schedule_ids"] = list(self.completed_schedule_ids[-100:])
        result["suspended_until"] = (
            format_datetime(self.suspended_until) if self.suspended_until is not None else None
        )
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeState":
        _strict_keys(
            data,
            {
                "schema_version", "automatic_shutdown_enabled", "suspended", "suspended_until",
                "owned_alarm_epoch", "owned_schedule_id", "completed_schedule_ids",
                "last_success", "last_error",
            },
            "state",
        )
        until = data.get("suspended_until")
        completed = data.get("completed_schedule_ids", [])
        if not isinstance(completed, list):
            raise ValidationError("completed_schedule_ids must be a list")
        if not all(isinstance(value, str) for value in completed):
            raise ValidationError("completed_schedule_ids values must be strings")
        owned_alarm = data.get("owned_alarm_epoch")
        if owned_alarm is not None and type(owned_alarm) is not int:
            raise ValidationError("owned_alarm_epoch must be an integer or null")
        return cls(
            schema_version=_schema_version(data, "state"),
            automatic_shutdown_enabled=_boolean(data, "automatic_shutdown_enabled", True),
            suspended=_boolean(data, "suspended", False),
            suspended_until=parse_datetime(str(until), field="suspended_until") if until else None,
            owned_alarm_epoch=owned_alarm,
            owned_schedule_id=data.get("owned_schedule_id"),
            completed_schedule_ids=tuple(str(value) for value in completed[-100:]),
            last_success=data.get("last_success"),
            last_error=data.get("last_error"),
        )


@dataclass(frozen=True, slots=True)
class NextAction:
    schedule_id: str
    shutdown_at: datetime
    wake_at: datetime
    due: bool
    conflicting_schedule_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "shutdown_at": format_datetime(self.shutdown_at),
            "wake_at": format_datetime(self.wake_at),
            "due": self.due,
            "conflicting_schedule_ids": list(self.conflicting_schedule_ids),
        }


@dataclass(frozen=True, slots=True)
class OperationResult:
    outcome: str
    reason: str
    schedule_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def shutdown_requested(self) -> bool:
        return self.outcome == "shutdown_requested"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
