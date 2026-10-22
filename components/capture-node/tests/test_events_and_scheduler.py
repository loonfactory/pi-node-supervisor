from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, time as DailyTime, timedelta

import pytest

from capture_node.core import events
from capture_node.core.events import EVENT_ATTRIBUTE, EventEmitter, EventType
from capture_node.core.scheduler import (
    CaptureSchedule,
    CaptureScheduler,
    IntervalCaptureSchedule,
    OnceCaptureSchedule,
    next_daily_occurrence,
    next_window_occurrence,
)


@pytest.fixture
def korea_system_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    with monkeypatch.context() as context:
        context.setenv("TZ", "Asia/Seoul")
        time.tzset()
        yield
    time.tzset()


@pytest.mark.covers("NFR-006")
def test_x7_importing_core_does_not_configure_global_logging() -> None:
    root = logging.getLogger()
    before = tuple(root.handlers)

    EventEmitter().emit(EventType.STARTED)

    assert tuple(root.handlers) == before
    assert any(isinstance(handler, logging.NullHandler) for handler in events.logger.handlers)


@pytest.mark.covers("CAP-001", "NFR-007")
def test_b3_fixed_rate_scheduler_keeps_deadlines() -> None:
    ticks: list[float] = []
    complete = threading.Event()
    started = time.monotonic()

    def tick() -> None:
        ticks.append(time.monotonic())
        if len(ticks) == 4:
            complete.set()

    scheduler = CaptureScheduler(
        CaptureSchedule((IntervalCaptureSchedule(0.025),)),
        tick,
    )
    scheduler.start()
    assert complete.wait(0.4)
    scheduler.stop()

    offsets = [tick_time - started for tick_time in ticks[:4]]
    assert offsets == pytest.approx([0.025, 0.05, 0.075, 0.1], abs=0.018)


@pytest.mark.covers("CAP-001")
def test_next_daily_occurrence_uses_system_local_time(
    korea_system_timezone,
) -> None:
    times = (DailyTime(8, 30), DailyTime(17, 45, 30))

    morning = next_daily_occurrence(
        datetime(2026, 7, 24, 1, 0, tzinfo=UTC),
        times,
    )
    evening = next_daily_occurrence(
        datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
        times,
    )

    assert morning.isoformat() == "2026-07-24T17:45:30+09:00"
    assert evening.isoformat() == "2026-07-25T08:30:00+09:00"


@pytest.mark.covers("CAP-001")
def test_empty_scheduler_waits_until_a_schedule_is_added() -> None:
    captured = threading.Event()
    scheduler = CaptureScheduler(
        CaptureSchedule(),
        captured.set,
    )

    scheduler.start()
    assert not captured.wait(0.05)
    scheduler.update_schedule(
        CaptureSchedule((IntervalCaptureSchedule(0.01),))
    )
    assert captured.wait(0.2)
    scheduler.stop()


@pytest.mark.covers("CAP-001")
def test_once_schedule_runs_once_per_day() -> None:
    captured = threading.Event()
    current = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)
    scheduler = CaptureScheduler(
        CaptureSchedule((OnceCaptureSchedule(DailyTime(10, 0)),)),
        captured.set,
        now=lambda: current,
    )

    scheduler.start()
    assert not captured.wait(0.05)
    scheduler.update_schedule(
        CaptureSchedule((IntervalCaptureSchedule(0.01),))
    )
    assert captured.wait(0.2)
    scheduler.stop()


@pytest.mark.covers("CAP-001")
def test_next_daily_interval_occurrence_includes_window_bounds(
    korea_system_timezone,
) -> None:
    first = next_window_occurrence(
        datetime(2026, 7, 24, 2, 59, 59, tzinfo=UTC),
        DailyTime(12, 0),
        DailyTime(12, 2),
        60,
    )
    second = next_window_occurrence(
        first,
        DailyTime(12, 0),
        DailyTime(12, 2),
        60,
    )
    last = next_window_occurrence(
        second,
        DailyTime(12, 0),
        DailyTime(12, 2),
        60,
    )
    next_day = next_window_occurrence(
        last,
        DailyTime(12, 0),
        DailyTime(12, 2),
        60,
    )

    assert first.isoformat() == "2026-07-24T12:00:00+09:00"
    assert second.isoformat() == "2026-07-24T12:01:00+09:00"
    assert last.isoformat() == "2026-07-24T12:02:00+09:00"
    assert next_day.isoformat() == "2026-07-25T12:00:00+09:00"


@pytest.mark.covers("CAP-001")
def test_scheduler_coalesces_rules_due_at_the_same_time() -> None:
    target = datetime.now().astimezone() + timedelta(seconds=0.5)
    target_time = target.timetz().replace(tzinfo=None)
    ticks: list[datetime] = []
    captured = threading.Event()

    def tick() -> None:
        ticks.append(datetime.now(UTC))
        captured.set()

    scheduler = CaptureScheduler(
        CaptureSchedule(
            (
                OnceCaptureSchedule(target_time),
                IntervalCaptureSchedule(
                    60,
                    target_time,
                    target_time,
                ),
            )
        ),
        tick,
    )
    scheduler.start()
    assert captured.wait(2)
    scheduler.stop()

    assert len(ticks) == 1
