"""Supervisor-owned v2 schedule occurrence calculation."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .config_v2 import (
    DailyScheduleSource,
    IntervalScheduleSource,
    ResolvedConfiguration,
    ScheduleSource,
)


class Occurrence(BaseModel):
    model_config = ConfigDict(frozen=True)

    device_id: str
    device_kind: Literal["camera", "sensor"]
    scheduled_at: datetime

    @property
    def claim_id(self) -> str:
        return f"{self.device_kind}:{self.device_id}:{self.scheduled_at.isoformat()}"


class ScheduleEngine:
    def __init__(
        self,
        configuration: ResolvedConfiguration,
        *,
        local_timezone: tzinfo | None = None,
    ) -> None:
        self.configuration = configuration
        self.local_timezone = local_timezone or datetime.now().astimezone().tzinfo
        if self.local_timezone is None:
            raise ValueError("system local timezone is unavailable")

    def occurrences(self, start: datetime, end: datetime) -> tuple[Occurrence, ...]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("occurrence range must use timezone-aware datetimes")
        if end < start:
            raise ValueError("occurrence range end must not precede start")
        selected: dict[tuple[str, datetime], Occurrence] = {}
        for kind, devices in (
            ("camera", self.configuration.cameras),
            ("sensor", self.configuration.sensors),
        ):
            for device_id, device in devices.items():
                for rule in device.schedule:
                    for scheduled_at in self._rule_occurrences(rule, start, end):
                        selected[(device_id, scheduled_at)] = Occurrence(
                            device_id=device_id,
                            device_kind=kind,
                            scheduled_at=scheduled_at,
                        )
        return tuple(
            sorted(
                selected.values(),
                key=lambda item: (item.scheduled_at, item.device_id),
            )
        )

    def next_occurrences(
        self, after: datetime, *, horizon_days: int = 3
    ) -> tuple[Occurrence, ...]:
        if horizon_days < 1:
            raise ValueError("horizon_days must be positive")
        return self.occurrences(
            after,
            after + timedelta(days=horizon_days),
        )

    def _rule_occurrences(
        self, rule: ScheduleSource, start: datetime, end: datetime
    ) -> tuple[datetime, ...]:
        local_start = start.astimezone(self.local_timezone)
        local_end = end.astimezone(self.local_timezone)
        first_date = local_start.date() - timedelta(days=1)
        last_date = local_end.date()
        result: list[datetime] = []
        current_date = first_date
        while current_date <= last_date:
            if isinstance(rule, DailyScheduleSource):
                candidate = self._local_datetime(current_date, rule.time)
                self._append_if_selected(result, candidate, start, end)
            elif isinstance(rule, IntervalScheduleSource):
                interval_start = self._local_datetime(current_date, rule.start_time)
                interval_end = self._local_datetime(current_date, rule.end_time)
                if rule.end_time <= rule.start_time:
                    interval_end += timedelta(days=1)
                candidate = interval_start
                step = timedelta(seconds=rule.interval_seconds)
                while candidate <= interval_end:
                    self._append_if_selected(result, candidate, start, end)
                    candidate += step
            current_date += timedelta(days=1)
        return tuple(result)

    def _local_datetime(self, day: date, wall_time: time) -> datetime:
        return datetime.combine(day, wall_time, self.local_timezone)

    @staticmethod
    def _append_if_selected(
        result: list[datetime],
        candidate: datetime,
        start: datetime,
        end: datetime,
    ) -> None:
        utc_candidate = candidate.astimezone(UTC)
        if start.astimezone(UTC) <= utc_candidate <= end.astimezone(UTC):
            result.append(utc_candidate)
