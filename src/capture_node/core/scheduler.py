"""Core interval and daily-time scheduling."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time as Time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class CaptureSchedule:
    mode: Literal["interval", "daily", "disabled"]
    interval_seconds: float | None = None
    daily_times: tuple[Time, ...] = ()
    timezone: str = "Asia/Seoul"

    def __post_init__(self) -> None:
        if self.mode == "interval":
            if self.interval_seconds is None or self.interval_seconds <= 0:
                raise ValueError("interval schedule requires a positive interval")
            if self.daily_times:
                raise ValueError("interval schedule cannot contain daily times")
        elif self.mode == "daily":
            if self.interval_seconds is not None:
                raise ValueError("daily schedule cannot contain an interval")
            if not self.daily_times:
                raise ValueError("daily schedule requires at least one time")
            ZoneInfo(self.timezone)
        else:
            if self.interval_seconds is not None or self.daily_times:
                raise ValueError("disabled schedule cannot contain triggers")


def next_daily_occurrence(
    now: datetime,
    daily_times: tuple[Time, ...],
    timezone: str,
) -> datetime:
    """Return the next strictly future daily trigger in the selected timezone."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not daily_times:
        raise ValueError("daily_times cannot be empty")
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)
    for day_offset in (0, 1):
        date = local_now.date() + timedelta(days=day_offset)
        for scheduled_time in sorted(daily_times):
            candidate = datetime.combine(date, scheduled_time, tzinfo=zone)
            if candidate > local_now:
                return candidate
    raise AssertionError("a daily schedule must have a next occurrence")


class CaptureScheduler:
    """Runs either a monotonic interval or timezone-aware daily schedule."""

    def __init__(
        self,
        schedule: CaptureSchedule,
        tick: Callable[[], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._schedule = schedule
        self._tick = tick
        self._clock = clock
        self._now = now
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._revision = 0

    @property
    def schedule(self) -> CaptureSchedule:
        with self._condition:
            return self._schedule

    def update_schedule(self, schedule: CaptureSchedule) -> None:
        with self._condition:
            self._schedule = schedule
            self._revision += 1
            self._condition.notify_all()

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("scheduler is already started")
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run,
                name="capture-node-scheduler",
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
        with self._condition:
            schedule = self._schedule
            revision = self._revision
        deadline = self._next_deadline(schedule)

        while True:
            with self._condition:
                while not self._stopping:
                    remaining = self._remaining(schedule, deadline)
                    if remaining <= 0:
                        break
                    wait_seconds = (
                        None
                        if schedule.mode == "disabled"
                        else (
                            remaining
                            if schedule.mode == "interval"
                            else min(remaining, 60)
                        )
                    )
                    self._condition.wait(wait_seconds)
                    if self._revision != revision:
                        revision = self._revision
                        schedule = self._schedule
                        deadline = self._next_deadline(schedule)
                if self._stopping:
                    return

            self._tick()
            if schedule.mode == "interval":
                assert isinstance(deadline, float)
                assert schedule.interval_seconds is not None
                deadline += schedule.interval_seconds
                now = self._clock()
                if deadline <= now:
                    missed = int(
                        (now - deadline) // schedule.interval_seconds
                    ) + 1
                    deadline += missed * schedule.interval_seconds
            elif schedule.mode == "daily":
                deadline = self._next_deadline(schedule)

    def _next_deadline(
        self,
        schedule: CaptureSchedule,
    ) -> float | datetime | None:
        if schedule.mode == "interval":
            assert schedule.interval_seconds is not None
            return self._clock() + schedule.interval_seconds
        if schedule.mode == "disabled":
            return None
        return next_daily_occurrence(
            self._now(),
            schedule.daily_times,
            schedule.timezone,
        )

    def _remaining(
        self,
        schedule: CaptureSchedule,
        deadline: float | datetime | None,
    ) -> float:
        if schedule.mode == "disabled":
            return float("inf")
        if schedule.mode == "interval":
            assert isinstance(deadline, float)
            return deadline - self._clock()
        assert isinstance(deadline, datetime)
        return (deadline - self._now().astimezone(deadline.tzinfo)).total_seconds()
