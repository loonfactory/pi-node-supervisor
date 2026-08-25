from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from pi_node_supervisor.config_v2 import resolve_configuration
from pi_node_supervisor.schedule_v2 import ScheduleEngine


KST = timezone(timedelta(hours=9))


def resolved_with_schedule(rules, *, two_cameras=False):
    devices = {
        "rgb": {"captureNode": {"camera": {"type": "picamera2", "cameraNum": 0}}}
    }
    if two_cameras:
        devices["noir"] = {
            "captureNode": {"camera": {"type": "picamera2", "cameraNum": 1}}
        }
    return resolve_configuration(
        {
            "schemaVersion": 2,
            "schedules": {"capture": rules},
            "cameras": {
                "defaults": {"schedule": "capture"},
                "devices": devices,
            },
            "power": {"minOffSeconds": 300},
        }
    ).resolved


def test_interval_includes_overnight_boundaries() -> None:
    resolved = resolved_with_schedule(
        [
            {
                "type": "interval",
                "startTime": "23:00:00",
                "endTime": "01:00:00",
                "intervalSeconds": 3600,
            }
        ]
    )
    engine = ScheduleEngine(resolved, local_timezone=KST)

    occurrences = engine.occurrences(
        datetime(2030, 1, 1, 13, 59, tzinfo=UTC),
        datetime(2030, 1, 1, 16, 1, tzinfo=UTC),
    )

    assert [item.scheduled_at for item in occurrences] == [
        datetime(2030, 1, 1, 14, 0, tzinfo=UTC),
        datetime(2030, 1, 1, 15, 0, tzinfo=UTC),
        datetime(2030, 1, 1, 16, 0, tzinfo=UTC),
    ]


def test_duplicate_rules_run_each_device_once_and_keep_devices_independent() -> None:
    resolved = resolved_with_schedule(
        [
            {"type": "daily", "time": "12:00:00"},
            {
                "type": "interval",
                "startTime": "12:00:00",
                "endTime": "12:00:00",
                "intervalSeconds": 86400,
            },
        ],
        two_cameras=True,
    )
    engine = ScheduleEngine(resolved, local_timezone=KST)

    occurrences = engine.occurrences(
        datetime(2030, 1, 1, 2, 59, tzinfo=UTC),
        datetime(2030, 1, 1, 3, 1, tzinfo=UTC),
    )

    assert [(item.device_id, item.scheduled_at) for item in occurrences] == [
        ("noir", datetime(2030, 1, 1, 3, 0, tzinfo=UTC)),
        ("rgb", datetime(2030, 1, 1, 3, 0, tzinfo=UTC)),
    ]
