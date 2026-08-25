"""Runtime power windows and fail-safe pi-power-manager handoff."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any, Callable, Protocol

from .config_v2 import ResolvedConfiguration
from .runtime_v2 import ActiveWork
from .schedule_v2 import ScheduleEngine


@dataclass(frozen=True)
class PowerInterval:
    start: datetime
    end: datetime


class PowerManager(Protocol):
    def suspend(self) -> None: ...
    def resume(self) -> None: ...
    def validate(self) -> None: ...


class PowerManagerCli:
    def __init__(
        self,
        executable: Path,
        config_path: Path,
        state_path: Path,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.executable = executable
        self.config_path = config_path
        self.state_path = state_path
        self.runner = runner

    def suspend(self) -> None:
        self._run("suspend")

    def resume(self) -> None:
        self._run("resume")

    def validate(self) -> None:
        self._run("validate-config")

    def _run(self, command: str) -> None:
        result = self.runner(
            [
                str(self.executable),
                "--config",
                str(self.config_path),
                "--state",
                str(self.state_path),
                command,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:1000]
            raise RuntimeError(f"pi-power-manager {command} failed: {detail}")


class PowerPlanV2:
    def __init__(
        self,
        configuration: ResolvedConfiguration,
        *,
        local_timezone: tzinfo,
    ) -> None:
        self.configuration = configuration
        self.local_timezone = local_timezone
        self.schedule = ScheduleEngine(configuration, local_timezone=local_timezone)

    def intervals(self, start: datetime, end: datetime) -> tuple[PowerInterval, ...]:
        power = self.configuration.power
        intervals = [
            PowerInterval(
                occurrence.scheduled_at - timedelta(seconds=power.boot_lead_seconds),
                occurrence.scheduled_at + timedelta(seconds=power.shutdown_grace_seconds),
            )
            for occurrence in self.schedule.occurrences(start, end)
        ]
        intervals.extend(self._configured_windows(start, end))
        return _merge_intervals(intervals, power.min_off_seconds)

    def _configured_windows(
        self, start: datetime, end: datetime
    ) -> list[PowerInterval]:
        local_start = start.astimezone(self.local_timezone)
        local_end = end.astimezone(self.local_timezone)
        current = local_start.date() - timedelta(days=1)
        result: list[PowerInterval] = []
        while current <= local_end.date():
            for window in self.configuration.power.windows:
                interval_start = datetime.combine(
                    current, window.start_time, self.local_timezone
                )
                interval_end = datetime.combine(current, window.end_time, self.local_timezone)
                if window.end_time <= window.start_time:
                    interval_end += timedelta(days=1)
                utc_start = interval_start.astimezone(UTC)
                utc_end = interval_end.astimezone(UTC)
                if utc_end >= start.astimezone(UTC) and utc_start <= end.astimezone(UTC):
                    result.append(PowerInterval(utc_start, utc_end))
            current += timedelta(days=1)
        return result

    def manager_document(
        self,
        now: datetime,
        *,
        timezone_name: str,
        horizon_days: int = 7,
    ) -> dict[str, Any]:
        intervals = self.intervals(
            now - timedelta(days=1), now + timedelta(days=horizon_days)
        )
        return {
            "schema_version": 2,
            "timezone": timezone_name,
            "minimum_lead_seconds": 60,
            "clock_tolerance_seconds": self.configuration.clock_tolerance_seconds,
            "power_schedule": [
                {
                    "power-on": interval.start.astimezone(self.local_timezone).replace(
                        tzinfo=None
                    ).isoformat(),
                    "power-down": interval.end.astimezone(self.local_timezone).replace(
                        tzinfo=None
                    ).isoformat(),
                }
                for interval in intervals
            ],
        }


class RuntimePowerCoordinator:
    def __init__(
        self,
        plan: PowerPlanV2,
        active_work: ActiveWork,
        manager: PowerManager,
        config_path: Path,
        *,
        timezone_name: str,
    ) -> None:
        self.plan = plan
        self.active_work = active_work
        self.manager = manager
        self.config_path = config_path
        self.timezone_name = timezone_name
        self.manager.suspend()

    def refresh(self, now: datetime) -> dict[str, Any]:
        document = self.plan.manager_document(now, timezone_name=self.timezone_name)
        _atomic_json(self.config_path, document)
        self.manager.validate()
        intervals = self.plan.intervals(
            now - timedelta(days=1), now + timedelta(days=7)
        )
        current = next(
            (item for item in intervals if item.start <= now.astimezone(UTC) <= item.end),
            None,
        )
        if current is not None:
            return {"state": "within-window", "activeWork": self.active_work.count}
        future = [item for item in intervals if item.start > now.astimezone(UTC)]
        if not future:
            return {"state": "no-future-window", "activeWork": self.active_work.count}
        if self.active_work.count:
            return {"state": "waiting-for-work", "activeWork": self.active_work.count}
        next_interval = future[0]
        remaining = (next_interval.start - now.astimezone(UTC)).total_seconds()
        if remaining < self.plan.configuration.power.min_off_seconds:
            return {
                "state": "too-close-to-next-window",
                "activeWork": 0,
                "nextStart": next_interval.start.isoformat(),
            }
        if not self.active_work.begin_shutdown_if_idle():
            return {"state": "waiting-for-work", "activeWork": self.active_work.count}
        try:
            self.manager.resume()
        except BaseException:
            self.active_work.cancel_shutdown()
            raise
        return {
            "state": "ready-to-shutdown",
            "activeWork": 0,
            "nextStart": next_interval.start.isoformat(),
        }


def _merge_intervals(
    intervals: list[PowerInterval], min_off_seconds: int
) -> tuple[PowerInterval, ...]:
    if not intervals:
        return ()
    ordered = sorted(intervals, key=lambda item: (item.start, item.end))
    merged = [ordered[0]]
    for item in ordered[1:]:
        previous = merged[-1]
        gap = (item.start - previous.end).total_seconds()
        if item.start <= previous.end or gap < min_off_seconds:
            merged[-1] = PowerInterval(previous.start, max(previous.end, item.end))
        else:
            merged.append(item)
    return tuple(merged)


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
