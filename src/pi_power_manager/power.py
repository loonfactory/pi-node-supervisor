from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import datetime
from typing import Callable, Protocol

from .errors import PowerManagerError, PowerRequestError
from .models import Configuration, OneShotSchedule, OperationResult, RuntimeState
from .rtc import RTC
from .timeutil import ensure_utc, format_datetime


class PowerAdapter(Protocol):
    def poweroff(self) -> None: ...


class SystemdPowerAdapter:
    def poweroff(self) -> None:
        try:
            subprocess.run(["systemctl", "poweroff"], check=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PowerRequestError(f"systemctl poweroff failed: {exc}") from exc


class DryRunPowerAdapter:
    def __init__(self) -> None:
        self.request_count = 0

    def poweroff(self) -> None:
        self.request_count += 1


class PowerCoordinator:
    def __init__(self, rtc: RTC, power: PowerAdapter):
        self.rtc = rtc
        self.power = power

    def execute(
        self,
        schedule: OneShotSchedule,
        configuration: Configuration,
        state: RuntimeState,
        now: datetime,
        persist_prepared: Callable[[RuntimeState], None] | None = None,
    ) -> tuple[OperationResult, RuntimeState]:
        now = ensure_utc(now, field="now")
        if not state.automatic_shutdown_enabled:
            return self._keep_running("automatic shutdown is disabled", schedule, state)
        if state.is_suspended(now):
            return self._keep_running("automatic shutdown is suspended", schedule, state)
        if schedule.id in state.completed_schedule_ids:
            return self._keep_running("schedule was already completed", schedule, state)
        if schedule.wake_at <= now:
            return self._keep_running("next wake time is not in the future", schedule, state)
        current_state = state
        try:
            rtc_time = self.rtc.read_time()
            clock_delta = abs((rtc_time - now).total_seconds())
            if clock_delta > configuration.clock_tolerance_seconds:
                return self._keep_running(
                    f"system and RTC clocks differ by {clock_delta:.3f} seconds",
                    schedule,
                    state,
                    {"clock_delta_seconds": clock_delta},
                )
            alarm_epoch = self.rtc.set_alarm(
                schedule.wake_at,
                now=now,
                minimum_lead_seconds=configuration.minimum_lead_seconds,
                owned_alarm_epoch=state.owned_alarm_epoch,
            )
            prepared = replace(
                state,
                owned_alarm_epoch=alarm_epoch,
                owned_schedule_id=schedule.id,
                last_error=None,
            )
            if persist_prepared is not None:
                persist_prepared(prepared)
            current_state = prepared
            self.power.poweroff()
        except PowerManagerError as exc:
            return self._keep_running(
                str(exc), schedule, current_state, {"error_type": type(exc).__name__}
            )
        completed = replace(
            prepared,
            completed_schedule_ids=(*prepared.completed_schedule_ids, schedule.id)[-100:],
            last_success={
                "operation": "shutdown_requested",
                "schedule_id": schedule.id,
                "at": format_datetime(now),
                "wake_at": format_datetime(schedule.wake_at),
            },
        )
        return (
            OperationResult(
                "shutdown_requested", "wake alarm verified before power-off request", schedule.id,
                {"wake_alarm_epoch": alarm_epoch},
            ),
            completed,
        )

    @staticmethod
    def _keep_running(
        reason: str,
        schedule: OneShotSchedule,
        state: RuntimeState,
        details: dict | None = None,
    ) -> tuple[OperationResult, RuntimeState]:
        error = {"stage": "managed_shutdown", "schedule_id": schedule.id, "reason": reason}
        return (
            OperationResult("kept_running", reason, schedule.id, details or {}),
            replace(state, last_error=error),
        )
