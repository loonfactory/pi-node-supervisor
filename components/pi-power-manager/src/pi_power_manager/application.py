from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .diagnostics import diagnose
from .errors import PowerManagerError
from .models import RuntimeState
from .power import PowerAdapter, PowerCoordinator
from .rtc import RTC
from .scheduler import Scheduler
from .storage import JsonStore
from .timeutil import format_datetime


class Application:
    def __init__(self, store: JsonStore, rtc: RTC, power: PowerAdapter):
        self.store = store
        self.rtc = rtc
        self.scheduler = Scheduler()
        self.coordinator = PowerCoordinator(rtc, power)

    def suspend(self, until: datetime | None) -> RuntimeState:
        state = self.store.load_state()
        updated = replace(state, suspended=True, suspended_until=until)
        self.store.save_state(updated)
        return updated

    def resume(self) -> RuntimeState:
        state = self.store.load_state()
        updated = replace(state, suspended=False, suspended_until=None)
        self.store.save_state(updated)
        return updated

    def status(self, now: datetime) -> dict[str, Any]:
        config = self.store.load_config()
        state = self.store.load_state()
        action = self.scheduler.next_action(config, state, now)
        alarm: int | None = None
        rtc_error: str | None = None
        try:
            alarm = self.rtc.read_alarm()
        except PowerManagerError as exc:
            rtc_error = str(exc)
        return {
            "now": format_datetime(now),
            "timezone": config.timezone,
            "power_schedule": config.to_dict()["power_schedule"],
            "automatic_shutdown_enabled": state.automatic_shutdown_enabled,
            "suspended": state.is_suspended(now),
            "suspended_until": format_datetime(state.suspended_until) if state.suspended_until else None,
            "next_action": action.to_dict() if action else None,
            "wake_alarm_epoch": alarm,
            "owned_alarm_epoch": state.owned_alarm_epoch,
            "rtc_error": rtc_error,
            "last_success": state.last_success,
            "last_error": state.last_error,
        }

    def dry_run(self, now: datetime) -> dict[str, Any]:
        config = self.store.load_config()
        state = self.store.load_state()
        action = self.scheduler.next_action(config, state, now)
        blockers: list[str] = []
        if not state.automatic_shutdown_enabled:
            blockers.append("automatic shutdown is disabled")
        if state.is_suspended(now):
            blockers.append("automatic shutdown is suspended")
        if action is None:
            blockers.append("no future schedule")
        else:
            if action.conflicting_schedule_ids:
                blockers.append(
                    "overlapping schedules require the device to remain running: "
                    + ", ".join(action.conflicting_schedule_ids)
                )
            if (action.wake_at - now).total_seconds() < config.minimum_lead_seconds:
                blockers.append("wake time does not meet minimum lead time")
        return {
            "at": format_datetime(now),
            "next_action": action.to_dict() if action else None,
            "would_attempt_shutdown": bool(action and action.due and not blockers),
            "blockers": blockers,
        }

    def apply_due(self, now: datetime) -> dict[str, Any]:
        config = self.store.load_config()
        state = self.store.load_state()
        action = self.scheduler.next_action(config, state, now)
        if action is None or not action.due:
            return {"outcome": "kept_running", "reason": "no schedule is due"}
        if action.conflicting_schedule_ids:
            result = {
                "outcome": "kept_running",
                "reason": "overlapping schedules require the device to remain running",
                "conflicting_schedule_ids": list(action.conflicting_schedule_ids),
            }
            self.store.save_state(replace(
                state,
                last_error={
                    "stage": "schedule_conflict",
                    "schedule_id": action.schedule_id,
                    "reason": result["reason"],
                    "conflicting_schedule_ids": result["conflicting_schedule_ids"],
                },
            ))
            return result
        diagnostic = self.diagnose()
        if not diagnostic["automatic_shutdown_ready"]:
            result = {
                "outcome": "kept_running",
                "reason": "required compatibility diagnostics did not pass",
                "failed_checks": [
                    item for item in diagnostic["checks"] if item["status"] != "pass"
                ],
            }
            self.store.save_state(replace(
                state,
                last_error={
                    "stage": "diagnostics",
                    "schedule_id": action.schedule_id,
                    "reason": result["reason"],
                },
            ))
            return result
        schedule = self.scheduler.find(config, action.schedule_id)
        if schedule is None:
            return {"outcome": "kept_running", "reason": "due schedule disappeared"}
        result, updated_state = self.coordinator.execute(
            schedule, config, state, now, persist_prepared=self.store.save_state
        )
        self.store.save_state(updated_state)
        return result.to_dict()

    def diagnose(self) -> dict[str, Any]:
        return diagnose(self.rtc, self.store.config_path, self.store.state_path)
