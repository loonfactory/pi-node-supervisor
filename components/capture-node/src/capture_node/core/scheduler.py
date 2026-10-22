"""Core scheduling for daily capture rules."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time as Time, timedelta


@dataclass(frozen=True, slots=True)
class OnceCaptureSchedule:
    """Triggers once per day at one system-local wall-clock time."""

    time: Time

    def __post_init__(self) -> None:
        if self.time.tzinfo is not None:
            raise ValueError("capture time must not include an offset")


@dataclass(frozen=True, slots=True)
class IntervalCaptureSchedule:
    """Triggers continuously or within one daily system-local time window."""

    interval: float
    start_time: Time | None = None
    end_time: Time | None = None

    def __post_init__(self) -> None:
        if self.interval <= 0:
            raise ValueError("capture interval must be positive")
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start and end times must be configured together")
        if self.start_time is not None and self.start_time.tzinfo is not None:
            raise ValueError("capture time must not include an offset")
        if self.end_time is not None and self.end_time.tzinfo is not None:
            raise ValueError("capture time must not include an offset")
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time < self.start_time
        ):
            raise ValueError("end time must not be earlier than start time")


CaptureRule = OnceCaptureSchedule | IntervalCaptureSchedule


@dataclass(frozen=True, slots=True)
class CaptureSchedule:
    rules: tuple[CaptureRule, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.rules)) != len(self.rules):
            raise ValueError("capture schedules must be unique")


def next_daily_occurrence(
    now: datetime,
    times: tuple[Time, ...],
) -> datetime:
    """Return the next strictly future trigger in the system local time."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not times:
        raise ValueError("times cannot be empty")
    local_now = now.astimezone()
    for day_offset in (0, 1):
        date = local_now.date() + timedelta(days=day_offset)
        for scheduled_time in sorted(times):
            candidate = datetime.combine(date, scheduled_time).astimezone()
            if candidate > now:
                return candidate
    raise AssertionError("a daily schedule must have a next occurrence")


def next_window_occurrence(
    now: datetime,
    start_time: Time,
    end_time: Time,
    interval: float,
) -> datetime:
    """Return the next strictly future trigger in a daily local-time window."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if interval <= 0:
        raise ValueError("capture interval must be positive")
    if end_time < start_time:
        raise ValueError("end time must not be earlier than start time")
    local_now = now.astimezone()
    for day_offset in (0, 1):
        date = local_now.date() + timedelta(days=day_offset)
        start = datetime.combine(date, start_time).astimezone()
        end = datetime.combine(date, end_time).astimezone()
        if now < start:
            return start
        elapsed = (now - start).total_seconds()
        step = math.floor(elapsed / interval) + 1
        candidate = start + timedelta(seconds=step * interval)
        if candidate <= end:
            return candidate
    raise AssertionError("a daily interval must have a next occurrence")


class CaptureScheduler:
    """Runs multiple capture rules and coalesces simultaneous triggers."""

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
        deadlines = self._next_deadlines(schedule)

        while True:
            with self._condition:
                while not self._stopping:
                    remaining = [
                        self._remaining(rule, deadline)
                        for rule, deadline in zip(
                            schedule.rules,
                            deadlines,
                            strict=True,
                        )
                    ]
                    next_wait = min(remaining, default=float("inf"))
                    if next_wait <= 0:
                        break
                    wait_seconds = (
                        None if math.isinf(next_wait) else min(next_wait, 60)
                    )
                    self._condition.wait(wait_seconds)
                    if self._revision != revision:
                        revision = self._revision
                        schedule = self._schedule
                        deadlines = self._next_deadlines(schedule)
                if self._stopping:
                    return

            due = [
                index
                for index, (rule, deadline) in enumerate(
                    zip(schedule.rules, deadlines, strict=True)
                )
                if self._remaining(rule, deadline) <= 0
            ]
            self._tick()
            for index in due:
                deadlines[index] = self._advance_deadline(
                    schedule.rules[index],
                    deadlines[index],
                )

    def _next_deadlines(
        self,
        schedule: CaptureSchedule,
    ) -> list[float | datetime]:
        return [self._next_deadline(rule) for rule in schedule.rules]

    def _next_deadline(self, rule: CaptureRule) -> float | datetime:
        if isinstance(rule, OnceCaptureSchedule):
            return next_daily_occurrence(self._now(), (rule.time,))
        if rule.start_time is None:
            return self._clock() + rule.interval
        assert rule.end_time is not None
        return next_window_occurrence(
            self._now(),
            rule.start_time,
            rule.end_time,
            rule.interval,
        )

    def _advance_deadline(
        self,
        rule: CaptureRule,
        deadline: float | datetime,
    ) -> float | datetime:
        if isinstance(rule, IntervalCaptureSchedule) and rule.start_time is None:
            assert isinstance(deadline, float)
            deadline += rule.interval
            now = self._clock()
            if deadline <= now:
                missed = int((now - deadline) // rule.interval) + 1
                deadline += missed * rule.interval
            return deadline
        return self._next_deadline(rule)

    def _remaining(
        self,
        rule: CaptureRule,
        deadline: float | datetime,
    ) -> float:
        if isinstance(rule, IntervalCaptureSchedule) and rule.start_time is None:
            assert isinstance(deadline, float)
            return deadline - self._clock()
        assert isinstance(deadline, datetime)
        return (deadline - self._now()).total_seconds()
