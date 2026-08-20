from __future__ import annotations

import pytest
from pydantic import ValidationError

from pi_node_supervisor.models import (
    CaptureScheduleSettings,
    DailyPowerKeepSchedule,
    NodeRegistryDocument,
    StorageSettingsDocument,
    SupervisorConfiguration,
)


def test_daily_power_keep_schedule_requires_forward_wall_clock_window():
    schedule = DailyPowerKeepSchedule.model_validate(
        {
            "id": "office-hours",
            "start": "09:00:00",
            "end": "10:00:00",
            "timezone": "Asia/Seoul",
        }
    )
    assert schedule.start.isoformat() == "09:00:00"

    with pytest.raises(ValidationError, match="end must be later"):
        DailyPowerKeepSchedule.model_validate(
            {
                "id": "invalid-hours",
                "start": "10:00:00",
                "end": "09:00:00",
                "timezone": "Asia/Seoul",
            }
        )


def test_configuration_rejects_unknown_keys():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SupervisorConfiguration.model_validate(
            {
                "schema_version": 1,
                "timezone": "UTC",
                "activities": [],
                "surprise": True,
            }
        )


def test_capture_node_settings_preserve_native_json_shape():
    document = SupervisorConfiguration.model_validate(
        {
            "schema_version": 1,
            "timezone": "Asia/Seoul",
            "capture_node_settings": {
                "common": {
                    "capture": {
                        "params": {
                            "horizontal_flip": True,
                            "vertical_flip": True,
                        }
                    }
                },
                "nodes": {
                    "camera0": {
                        "calibration": {
                            "type": "checkerboard",
                            "camera_matrix": [[1, 0, 2], [0, 1, 3], [0, 0, 1]],
                        }
                    }
                },
            },
        }
    )

    settings = document.model_dump(mode="json")["capture_node_settings"]
    assert settings["common"]["capture"]["params"]["horizontal_flip"] is True
    assert settings["nodes"]["camera0"]["calibration"]["type"] == "checkerboard"


def test_configuration_rejects_duplicate_activity_ids():
    activity = {
        "id": "capture",
        "type": "capture",
        "at": "2030-01-01T08:00:00",
        "node": "camera0",
        "profile": "default",
    }
    with pytest.raises(ValidationError, match="activity ids must be unique"):
        SupervisorConfiguration.model_validate(
            {
                "schema_version": 1,
                "timezone": "UTC",
                "activities": [activity, activity],
            }
        )


def test_node_registry_rejects_duplicate_nodes():
    node = {
        "id": "camera0",
        "kind": "capture-node",
        "activity_types": ["capture"],
        "profiles": ["default"],
    }
    with pytest.raises(ValidationError, match="node ids must be unique"):
        NodeRegistryDocument.model_validate(
            {"schema_version": 1, "nodes": [node, node]}
        )


def test_identifier_blocks_shell_shaped_values():
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        NodeRegistryDocument.model_validate(
            {
                "schema_version": 1,
                "nodes": [
                    {
                        "id": "camera0; shutdown now",
                        "kind": "capture-node",
                        "activity_types": ["capture"],
                    }
                ],
            }
        )


def test_managed_capture_requires_matching_loopback_endpoint(tmp_path):
    document = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "camera0",
                "kind": "capture-node",
                "activity_types": ["capture"],
                "endpoint": "http://127.0.0.1:8001",
                "managed_capture": {
                    "config_path": str(tmp_path / "camera.json"),
                    "host": "127.0.0.1",
                    "port": 8000,
                },
            }
        ],
    }
    with pytest.raises(ValidationError, match="endpoint must match"):
        NodeRegistryDocument.model_validate(document)


def test_storage_settings_validate_names_paths_and_authentication():
    with pytest.raises(ValidationError, match="storage names must be unique"):
        StorageSettingsDocument.model_validate(
            {
                "items": [
                    {"type": "file", "name": "archive", "directory": "captures"},
                    {"type": "file", "name": "archive", "directory": "backup"},
                ]
            }
        )
    with pytest.raises(ValidationError, match="must not traverse"):
        StorageSettingsDocument.model_validate(
            {"items": [{"type": "file", "name": "local", "directory": "../private"}]}
        )
    with pytest.raises(ValidationError, match="private key or password"):
        StorageSettingsDocument.model_validate(
            {
                "items": [
                    {
                        "type": "sftp",
                        "name": "archive",
                        "host": "storage.local",
                        "username": "capture",
                        "remote_directory": "/captures",
                    }
                ]
            }
        )


def test_storage_settings_accept_supported_storage_types():
    settings = StorageSettingsDocument.model_validate(
        {
            "items": [
                {"type": "file", "name": "local", "directory": "captures"},
                {
                    "type": "sftp",
                    "name": "offsite",
                    "host": "storage.local",
                    "username": "capture",
                    "remote_directory": "/captures",
                    "private_key": "~/.ssh/id_ed25519",
                },
                {
                    "type": "ftps",
                    "name": "archive",
                    "host": "archive.local",
                    "username": "capture",
                    "remote_directory": "/camera",
                    "password_env": "CAPTURE_FTPS_PASSWORD",
                },
            ]
        }
    )
    assert [item.type for item in settings.items] == ["file", "sftp", "ftps"]


def test_storage_settings_accept_and_validate_retention():
    settings = StorageSettingsDocument.model_validate(
        {
            "items": [
                {"type": "file", "name": "local", "directory": "captures"}
            ],
            "retention": [{"target": "local", "max_age_days": 7}],
        }
    )
    assert settings.retention[0].max_age_days == 7
    with pytest.raises(ValidationError, match="retention target is not configured"):
        StorageSettingsDocument.model_validate(
            {
                "items": [
                    {"type": "file", "name": "local", "directory": "captures"}
                ],
                "retention": [{"target": "missing", "max_age_days": 7}],
            }
        )


def test_capture_schedule_supports_capture_node_modes():
    interval = CaptureScheduleSettings.model_validate(
        {
            "mode": "interval",
            "interval_seconds": 2.5,
            "daily_times": [],
            "timezone": "Asia/Seoul",
        }
    )
    daily = CaptureScheduleSettings.model_validate(
        {
            "mode": "daily",
            "interval_seconds": None,
            "daily_times": ["08:30:00", "17:45:30"],
            "timezone": "Asia/Seoul",
        }
    )
    disabled = CaptureScheduleSettings.model_validate(
        {
            "mode": "disabled",
            "interval_seconds": None,
            "daily_times": [],
            "timezone": "UTC",
        }
    )
    assert interval.interval_seconds == 2.5
    assert [value.isoformat() for value in daily.daily_times] == [
        "08:30:00",
        "17:45:30",
    ]
    assert disabled.mode == "disabled"


@pytest.mark.parametrize(
    "document",
    [
        {"mode": "interval", "interval_seconds": None, "daily_times": []},
        {"mode": "interval", "interval_seconds": 10, "daily_times": ["08:30"]},
        {"mode": "daily", "interval_seconds": 10, "daily_times": []},
        {"mode": "daily", "interval_seconds": None, "daily_times": ["08:30", "08:30"]},
        {"mode": "disabled", "interval_seconds": 10, "daily_times": []},
    ],
)
def test_capture_schedule_rejects_invalid_mode_fields(document):
    with pytest.raises(ValidationError):
        CaptureScheduleSettings.model_validate(
            {"timezone": "Asia/Seoul", **document}
        )
