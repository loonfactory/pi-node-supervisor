from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pi_node_supervisor.errors import PlanningError
from pi_node_supervisor.models import SupervisorConfiguration
from pi_node_supervisor.planner import Planner

from conftest import configuration


def capture(activity_id="capture", at="2030-01-01T08:00:00", **extra):
    return {
        "id": activity_id,
        "type": "capture",
        "at": at,
        "node": "camera0",
        "profile": "default",
        **extra,
    }


def sensor(activity_id="sensor", at="2030-01-01T08:20:00", **extra):
    return {
        "id": activity_id,
        "type": "sensor-collection",
        "at": at,
        "node": "environment0",
        "profile": "temperature-humidity",
        **extra,
    }


def recurring(schedule):
    return SupervisorConfiguration.model_validate(
        {
            "schema_version": 1,
            "timezone": "UTC",
            "policies": {
                "boot-lead-seconds": 120,
                "shutdown-grace-seconds": 60,
                "merge-power-gaps-shorter-than-seconds": 1800,
                "recurrence-horizon-hours": 24,
            },
            "activities": [],
            "capture_schedules": [schedule],
        }
    )


def test_planner_applies_node_and_policy_lead_times(registry):
    plan = Planner(registry).plan(configuration(capture()))
    activity = plan.activities[0]
    assert activity.requested_start == datetime(2029, 12, 31, 23, 0, tzinfo=UTC)
    assert activity.prepare_at == datetime(2029, 12, 31, 22, 59, 55, tzinfo=UTC)
    assert activity.power_on == datetime(2029, 12, 31, 22, 57, 40, tzinfo=UTC)
    assert activity.complete_at == datetime(2029, 12, 31, 23, 0, 5, tzinfo=UTC)
    assert activity.release_at == datetime(2029, 12, 31, 23, 0, 15, tzinfo=UTC)
    assert activity.power_down == datetime(2029, 12, 31, 23, 1, tzinfo=UTC)


def test_capture_shutdown_grace_is_measured_from_scheduled_time(registry):
    plan = Planner(registry).plan(
        configuration(capture(), merge_seconds=0)
    )
    activity = plan.activities[0]

    assert activity.requested_start == datetime(2029, 12, 31, 23, 0, tzinfo=UTC)
    assert activity.power_down - activity.requested_start == timedelta(seconds=60)


def test_capture_shutdown_never_precedes_node_release(registry):
    config = configuration(capture(), merge_seconds=0)
    config = config.model_copy(
        update={
            "policies": config.policies.model_copy(
                update={"shutdown_grace_seconds": 0}
            )
        }
    )
    activity = Planner(registry).plan(config).activities[0]

    assert activity.power_down == activity.release_at
    assert activity.power_down - activity.requested_start == timedelta(seconds=15)


def test_planner_merges_only_gaps_shorter_than_policy(registry):
    planner = Planner(registry)
    merged = planner.plan(configuration(capture(), sensor()))
    assert len(merged.power_windows) == 1
    assert merged.power_windows[0].activity_ids == ("capture", "sensor")
    assert any(
        "policy threshold is 1800s" in reason
        for reason in merged.power_windows[0].reasons
    )

    separate = planner.plan(
        configuration(capture(), sensor(at="2030-01-01T10:00:00"))
    )
    assert len(separate.power_windows) == 2
    assert separate.power_windows[0].automatic_shutdown_possible is True
    assert separate.power_windows[0].next_power_on == separate.power_windows[1].power_on
    assert separate.power_windows[1].automatic_shutdown_possible is False
    assert separate.power_windows[1].next_power_on is None


def test_exact_threshold_is_not_merged(registry):
    planner = Planner(registry)
    first = capture(duration_seconds=5)
    # camera power-down is 08:01:15; sensor power-on is 08:31:15, exactly 1800s later.
    second = sensor(at="2030-01-01T08:33:25")
    plan = planner.plan(configuration(first, second))
    assert len(plan.power_windows) == 2


def test_planner_sorts_input_deterministically(registry):
    plan = Planner(registry).plan(configuration(sensor(), capture()))
    assert [item.activity_id for item in plan.activities] == ["capture", "sensor"]
    assert plan.source_hash == Planner(registry).plan(
        configuration(sensor(), capture())
    ).source_hash


def test_unknown_node_profile_and_capability_are_rejected(registry):
    planner = Planner(registry)
    with pytest.raises(PlanningError, match="unknown node"):
        planner.plan(configuration(capture(node="missing")))
    with pytest.raises(PlanningError, match="does not provide profile"):
        planner.plan(configuration(capture(profile="missing")))
    with pytest.raises(PlanningError, match="does not support activity type"):
        planner.plan(configuration(capture(node="environment0")))


def test_same_node_execution_overlap_is_rejected(registry):
    planner = Planner(registry)
    with pytest.raises(PlanningError, match="activities overlap"):
        planner.plan(
            configuration(
                capture("first", duration_seconds=30),
                capture("second", at="2030-01-01T08:00:20"),
            )
        )


def test_maintenance_without_node_is_supported(registry):
    plan = Planner(registry).plan(
        configuration(
            {
                "id": "remote",
                "type": "maintenance-window",
                "start": "2030-01-01T13:00:00",
                "end": "2030-01-01T15:00:00",
            }
        )
    )
    assert plan.activities[0].node_id == "supervisor"
    assert (
        plan.activities[0].requested_end - plan.activities[0].requested_start
    ).total_seconds() == 7200


def test_maintenance_requires_forward_interval(registry):
    with pytest.raises(PlanningError, match="end must be later"):
        Planner(registry).plan(
            configuration(
                {
                    "id": "remote",
                    "type": "maintenance-window",
                    "start": "2030-01-01T15:00:00",
                    "end": "2030-01-01T13:00:00",
                }
            )
        )


def test_dst_gap_and_fold_require_safe_input(registry):
    planner = Planner(registry)
    with pytest.raises(PlanningError, match="does not exist"):
        planner.plan(
            configuration(
                capture(at="2030-03-10T02:30:00"),
                timezone="America/New_York",
            )
        )
    with pytest.raises(PlanningError, match="ambiguous"):
        planner.plan(
            configuration(
                capture(at="2030-11-03T01:30:00"),
                timezone="America/New_York",
            )
        )
    explicit = planner.plan(
        configuration(
            capture(at="2030-11-03T01:30:00-04:00"),
            timezone="America/New_York",
        )
    )
    assert explicit.activities[0].requested_start.tzinfo == UTC


def test_power_manager_output_uses_only_real_schema_v2_fields(registry):
    planner = Planner(registry)
    config = configuration(capture(), sensor())
    output = planner.power_manager_configuration(config).model_dump(
        mode="json", by_alias=True
    )
    assert set(output) == {
        "schema_version",
        "timezone",
        "minimum_lead_seconds",
        "clock_tolerance_seconds",
        "power_schedule",
    }
    assert output["schema_version"] == 2
    assert set(output["power_schedule"][0]) == {"power-on", "power-down"}
    assert output["power_schedule"][0]["power-on"] == "2030-01-01T07:57:40"


def test_daily_capture_schedule_derives_capture_and_rtc_windows(registry):
    planner = Planner(registry)
    config = recurring(
        {
            "id": "camera0-daily",
            "node": "camera0",
            "profile": "default",
            "mode": "daily",
            "interval_seconds": None,
            "daily_times": ["08:00:00", "17:30:00"],
            "timezone": "Asia/Seoul",
        }
    )
    plan = planner.plan(config, now=datetime(2029, 12, 31, 20, tzinfo=UTC))

    assert all(item.activity_type == "capture-schedule" for item in plan.activities)
    first = plan.activities[0]
    assert first.requested_start == datetime(2029, 12, 31, 23, tzinfo=UTC)
    assert first.power_on == datetime(2029, 12, 31, 22, 57, 40, tzinfo=UTC)
    assert len(plan.power_windows) >= 2
    power = planner.power_manager_configuration(config, plan)
    assert power.power_schedule[0].power_on == "2029-12-31T22:57:40"


def test_empty_daily_capture_schedule_disables_automatic_power_plan(registry):
    planner = Planner(registry)
    config = recurring(
        {
            "id": "camera0-empty-daily",
            "node": "camera0",
            "profile": "default",
            "mode": "daily",
            "interval_seconds": None,
            "daily_times": [],
            "timezone": "Asia/Seoul",
        }
    )
    now = datetime(2030, 1, 1, tzinfo=UTC)
    plan = planner.plan(config, now=now)

    assert plan.activities == ()
    assert plan.power_windows == ()
    assert planner.power_manager_configuration(config, plan).power_schedule == ()


def test_daily_power_keep_schedule_uses_exact_wall_clock_window(registry):
    config = SupervisorConfiguration.model_validate(
        {
            "schema_version": 1,
            "timezone": "Asia/Seoul",
            "policies": {
                "boot-lead-seconds": 300,
                "shutdown-grace-seconds": 180,
                "merge-power-gaps-shorter-than-seconds": 0,
                "recurrence-horizon-hours": 24,
            },
            "activities": [],
            "power_keep_schedules": [
                {
                    "id": "office-hours",
                    "start": "09:00:00",
                    "end": "10:00:00",
                    "timezone": "Asia/Seoul",
                }
            ],
        }
    )
    plan = Planner(registry).plan(
        config, now=datetime(2030, 1, 1, tzinfo=UTC)
    )

    keep = next(item for item in plan.activities if item.activity_type == "power-keep")
    assert keep.power_on == datetime(2030, 1, 1, tzinfo=UTC)
    assert keep.power_down == datetime(2030, 1, 1, 1, tzinfo=UTC)
    assert keep.prepare_at == keep.power_on
    assert keep.release_at == keep.power_down
    assert any("office-hours" in item for item in plan.power_windows[0].activity_ids)


def test_daily_power_keep_schedule_runs_without_capture_times(registry):
    config = SupervisorConfiguration.model_validate(
        {
            "schema_version": 1,
            "timezone": "Asia/Seoul",
            "activities": [],
            "capture_schedules": [
                {
                    "id": "camera0-daily",
                    "node": "camera0",
                    "profile": "default",
                    "mode": "daily",
                    "interval_seconds": None,
                    "daily_times": [],
                    "timezone": "Asia/Seoul",
                }
            ],
            "power_keep_schedules": [
                {
                    "id": "maintenance-hours",
                    "start": "09:00:00",
                    "end": "10:00:00",
                    "timezone": "Asia/Seoul",
                }
            ],
        }
    )
    planner = Planner(registry)
    plan = planner.plan(config, now=datetime(2030, 1, 1, tzinfo=UTC))
    power = planner.power_manager_configuration(config, plan)

    assert not any(item.activity_type == "capture-schedule" for item in plan.activities)
    assert any(item.activity_type == "power-keep" for item in plan.activities)
    assert power.power_schedule


@pytest.mark.parametrize("interval_seconds", [10, 7200])
def test_interval_keeps_continuous_power_for_capture_node_timer(
    registry, interval_seconds
):
    planner = Planner(registry)
    config = recurring(
        {
            "id": "camera0-interval",
            "node": "camera0",
            "profile": "default",
            "mode": "interval",
            "interval_seconds": interval_seconds,
            "daily_times": [],
            "timezone": "UTC",
            "anchor_at": "2030-01-01T00:00:00Z",
        }
    )
    start = datetime(2030, 1, 1, tzinfo=UTC)
    plan = planner.plan(config, now=start)
    assert len(plan.activities) == 1
    assert plan.activities[0].activity_id == "camera0-interval"
    assert (plan.power_windows[0].power_down - plan.power_windows[0].power_on) > timedelta(hours=23)
    assert "native scheduler" in plan.activities[0].reasons[-1]
