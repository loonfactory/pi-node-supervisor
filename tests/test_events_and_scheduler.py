from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, time as DailyTime

import pytest

from capture_node.core import events
from capture_node.core.events import EVENT_ATTRIBUTE, EventEmitter, EventType
from capture_node.core.scheduler import (
    CaptureSchedule,
    CaptureScheduler,
    next_daily_occurrence,
)


@pytest.mark.covers("NFR-004")
def test_x6_all_event_types_have_stable_common_schema(event_output) -> None:
    emitter, collector = event_output

    for event_type in EventType:
        emitter.emit(event_type, sample=True)

    assert len(collector.items) == len(EventType)
    for event in collector.items:
        assert set(("schema_version", "event_type", "timestamp")) <= event.keys()
        assert event["schema_version"] == 1
        assert event["event_type"] in {item.value for item in EventType}


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
        CaptureSchedule(mode="interval", interval_seconds=0.025),
        tick,
    )
    scheduler.start()
    assert complete.wait(0.4)
    scheduler.stop()

    offsets = [tick_time - started for tick_time in ticks[:4]]
    assert offsets == pytest.approx([0.025, 0.05, 0.075, 0.1], abs=0.018)


@pytest.mark.covers("CAP-001")
def test_next_daily_occurrence_selects_today_then_tomorrow() -> None:
    times = (DailyTime(8, 30), DailyTime(17, 45, 30))

    morning = next_daily_occurrence(
        datetime(2026, 7, 24, 1, 0, tzinfo=UTC),
        times,
        "Asia/Seoul",
    )
    evening = next_daily_occurrence(
        datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
        times,
        "Asia/Seoul",
    )

    assert morning.isoformat() == "2026-07-24T17:45:30+09:00"
    assert evening.isoformat() == "2026-07-25T08:30:00+09:00"


@pytest.mark.covers("CAP-001")
def test_disabled_scheduler_waits_until_a_schedule_is_enabled() -> None:
    captured = threading.Event()
    scheduler = CaptureScheduler(
        CaptureSchedule(mode="disabled"),
        captured.set,
    )

    scheduler.start()
    assert not captured.wait(0.05)
    scheduler.update_schedule(CaptureSchedule(mode="interval", interval_seconds=0.01))
    assert captured.wait(0.2)
    scheduler.stop()
