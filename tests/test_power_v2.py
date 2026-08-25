from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest

from pi_node_supervisor.config_v2 import resolve_configuration
from pi_node_supervisor.power_v2 import (
    PowerManagerCli,
    PowerPlanV2,
    RuntimePowerCoordinator,
)
from pi_node_supervisor.runtime_v2 import ActiveWork


KST = timezone(timedelta(hours=9))


def test_power_manager_failure_includes_exit_status_when_output_is_empty(
    tmp_path: Path,
) -> None:
    manager = PowerManagerCli(
        Path("/opt/pi-power-manager"),
        tmp_path / "config.json",
        tmp_path / "state.json",
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 7, "", ""),
    )

    with pytest.raises(RuntimeError, match="exit status 7 with no output"):
        manager.resume()


def test_power_manager_hold_blocks_and_releases_privileged_timer(
    tmp_path: Path,
) -> None:
    calls = []
    hold = tmp_path / "supervisor.hold"
    manager = PowerManagerCli(
        Path("/opt/pi-power-manager"),
        tmp_path / "config.json",
        tmp_path / "state.json",
        hold,
        runner=lambda *args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    manager.suspend()
    manager.suspend()

    assert hold.is_file()
    assert calls == []

    manager.resume()

    assert not hold.exists()

    manager.validate()

    assert len(calls) == 1


def resolved():
    return resolve_configuration(
        {
            "schemaVersion": 2,
            "schedules": {
                "capture": [
                    {"type": "daily", "time": "12:00:00"},
                    {"type": "daily", "time": "12:04:00"},
                ]
            },
            "cameras": {
                "defaults": {"schedule": "capture"},
                "devices": {"rgb": {"captureNode": {}}},
            },
            "power": {
                "bootLeadSeconds": 30,
                "shutdownGraceSeconds": 30,
                "minOffSeconds": 300,
            },
        }
    ).resolved


def test_trigger_windows_merge_when_off_gap_is_too_short() -> None:
    plan = PowerPlanV2(resolved(), local_timezone=KST)

    intervals = plan.intervals(
        datetime(2030, 1, 1, 2, tzinfo=UTC),
        datetime(2030, 1, 1, 4, tzinfo=UTC),
    )

    assert intervals == (
        type(intervals[0])(
            datetime(2030, 1, 1, 2, 59, 30, tzinfo=UTC),
            datetime(2030, 1, 1, 3, 4, 30, tzinfo=UTC),
        ),
    )


def test_shutdown_handoff_waits_for_active_work(tmp_path: Path) -> None:
    class Manager:
        def __init__(self):
            self.calls = []

        def suspend(self):
            self.calls.append("suspend")

        def validate(self):
            self.calls.append("validate")

        def resume(self):
            self.calls.append("resume")

    active = ActiveWork()
    active.acquire()
    manager = Manager()
    coordinator = RuntimePowerCoordinator(
        PowerPlanV2(resolved(), local_timezone=KST),
        active,
        manager,
        tmp_path / "power.json",
        timezone_name="Asia/Seoul",
    )

    waiting = coordinator.refresh(datetime(2030, 1, 1, 3, 5, tzinfo=UTC))
    active.release()
    ready = coordinator.refresh(datetime(2030, 1, 1, 3, 5, tzinfo=UTC))

    assert waiting["state"] == "waiting-for-work"
    assert ready["state"] == "ready-to-shutdown"
    assert manager.calls[-1] == "resume"
