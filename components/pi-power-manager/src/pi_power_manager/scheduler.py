from __future__ import annotations

from datetime import datetime, timedelta

from .models import Configuration, NextAction, OneShotSchedule, RuntimeState
from .timeutil import ensure_utc


class Scheduler:
    def next_action(
        self,
        configuration: Configuration,
        state: RuntimeState,
        now: datetime,
        *,
        due_window_seconds: int = 60,
    ) -> NextAction | None:
        now = ensure_utc(now, field="now")
        candidates: list[NextAction] = []
        for schedule in configuration.schedules:
            if not schedule.enabled or schedule.id in state.completed_schedule_ids:
                continue
            if schedule.wake_at <= now:
                continue
            due = schedule.shutdown_at <= now < schedule.shutdown_at + timedelta(seconds=due_window_seconds)
            if schedule.shutdown_at > now or due:
                candidates.append(
                    NextAction(schedule.id, schedule.shutdown_at, schedule.wake_at, due)
                )
        if not candidates:
            return None
        selected = min(
            candidates, key=lambda action: (action.shutdown_at, action.wake_at, action.schedule_id)
        )
        conflicts = tuple(sorted(
            schedule.id
            for schedule in configuration.schedules
            if schedule.enabled
            and schedule.id != selected.schedule_id
            and schedule.id not in state.completed_schedule_ids
            and selected.shutdown_at < schedule.shutdown_at < selected.wake_at
        ))
        return NextAction(
            selected.schedule_id,
            selected.shutdown_at,
            selected.wake_at,
            selected.due,
            conflicts,
        )

    def find(self, configuration: Configuration, schedule_id: str) -> OneShotSchedule | None:
        return next((item for item in configuration.schedules if item.id == schedule_id), None)
