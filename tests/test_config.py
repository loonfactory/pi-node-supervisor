from __future__ import annotations

import json
from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from capture_node.core.cameras import Picamera2Options
from capture_node.core.config import (
    Configuration,
    ConfigurationError,
    JsonOptionsRepository,
    UnsupportedRuntimeChange,
)
from capture_node.core.options import Options
from conftest import make_configuration


def test_example_configuration_is_valid() -> None:
    repository_root = Path(__file__).resolve().parents[1]

    Options.model_validate_json(
        (repository_root / "config.example.json").read_text(encoding="utf-8")
    )


def test_picamera2_size_is_validated_by_its_options_model() -> None:
    options = Picamera2Options(camera_num=1, width=1920, height=1080)

    assert options.camera_num == 1
    assert options.size == (1920, 1080)
    with pytest.raises(ValidationError, match="configured together"):
        Picamera2Options(width=1920)
    with pytest.raises(ValidationError):
        Picamera2Options(camera_num=-1)


def test_raw_dng_is_disabled_by_default_and_restricted_to_picamera2() -> None:
    options = Options.model_validate(
        {
            "capture": {"interval_seconds": 60},
            "image": {"format": "png", "compression": 7},
        }
    )
    assert not options.image.raw.enabled

    document = options.model_dump(mode="json")
    document["camera"] = {
        "type": "v4l2",
        "device": "/dev/video0",
        "width": 1920,
        "height": 1080,
        "frame_rate": 15,
        "startup_timeout_seconds": 10,
    }
    document["image"]["raw"] = {"enabled": True, "format": "dng"}
    with pytest.raises(ValueError, match="requires a picamera2"):
        Options.model_validate(document)


@pytest.mark.covers("CFG-001", "CFG-002", "CFG-003", "CFG-004", "CFG-008")
def test_a1_loads_full_headless_configuration_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"capture": {"interval_seconds": 5}}),
        encoding="utf-8",
    )

    configuration = Configuration.load(path)

    assert configuration.current.capture.interval_seconds == 5
    assert configuration.current.camera.type == "picamera2"
    assert configuration.current.image.format == "png"
    assert configuration.current.image.compression == 7
    assert configuration.current.image.embed_metadata is True
    assert configuration.current.storages == ()
    assert configuration.current.maintenance.retention == ()


@pytest.mark.parametrize(
    "document",
    [
        {"capture": {"interval_seconds": 0}},
        {"capture": {"interval_seconds": 1, "unknown": True}},
        {"image": {"format": "jpeg"}},
        {
            "capture": {"interval_seconds": 1},
            "image": {"format": "png", "quality": 90},
        },
        {"capture": {"interval_seconds": 1}, "unknown": True},
    ],
)
@pytest.mark.covers("CFG-005", "CFG-006", "CFG-007")
def test_a2_rejects_invalid_unknown_and_missing_values(
    document: dict,
) -> None:
    with pytest.raises(ValidationError):
        Options.model_validate(document)


@pytest.mark.covers("CFG-007")
def test_a3_missing_configuration_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        Configuration.load(tmp_path / "missing.json")


@pytest.mark.covers("CAP-001", "CFG-001", "CFG-005")
def test_daily_capture_schedule_is_strictly_validated() -> None:
    options = Options.model_validate(
        {
            "capture": {
                "mode": "daily",
                "interval_seconds": None,
                "daily_times": ["08:30:00", "17:45:30"],
                "timezone": "Asia/Seoul",
            }
        }
    )

    assert options.capture.daily_times == (time(8, 30), time(17, 45, 30))

    invalid_documents = [
        {
            "capture": {
                "mode": "daily",
                "interval_seconds": 10,
                "daily_times": ["08:30:00"],
            }
        },
        {
            "capture": {
                "mode": "interval",
                "interval_seconds": 10,
                "daily_times": ["08:30:00"],
            }
        },
        {
            "capture": {
                "mode": "daily",
                "interval_seconds": None,
                "daily_times": ["08:30:00", "08:30:00"],
            }
        },
        {
            "capture": {
                "mode": "daily",
                "interval_seconds": None,
                "daily_times": ["08:30:00"],
                "timezone": "Not/A-Timezone",
            }
        },
    ]
    for document in invalid_documents:
        with pytest.raises(ValidationError):
            Options.model_validate(document)


@pytest.mark.parametrize("daily_times", [None, []])
def test_daily_schedule_without_times_waits_for_configuration(
    daily_times,
) -> None:
    options = Options.model_validate(
        {
            "capture": {
                "mode": "daily",
                "interval_seconds": None,
                "daily_times": daily_times,
            }
        }
    )

    assert options.capture.mode == "daily"
    assert options.capture.daily_times == ()


@pytest.mark.covers("CAP-001", "CFG-001", "CFG-005")
def test_disabled_capture_schedule_has_no_automatic_triggers() -> None:
    options = Options.model_validate(
        {
            "capture": {
                "mode": "disabled",
                "interval_seconds": None,
                "daily_times": [],
            }
        }
    )

    assert options.capture.mode == "disabled"
    assert options.capture.interval_seconds is None
    assert options.capture.daily_times == ()

    for capture in (
        {
            "mode": "disabled",
            "interval_seconds": 10,
            "daily_times": [],
        },
        {
            "mode": "disabled",
            "interval_seconds": None,
            "daily_times": ["08:30:00"],
        },
    ):
        with pytest.raises(ValidationError):
            Options.model_validate({"capture": capture})


@pytest.mark.covers("CFG-009", "CFG-010")
def test_d1_valid_runtime_change_is_persisted_before_application(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    configuration = make_configuration(path)
    observed_file: list[dict] = []

    def apply(_capture) -> None:
        observed_file.append(json.loads(path.read_text(encoding="utf-8")))

    result = configuration.replace(
        {
            **configuration.current.model_dump(mode="json"),
            "capture": {"interval_seconds": 3, "params": {"Brightness": 0.25}},
        },
        apply,
    )

    assert result.succeeded
    assert observed_file[0]["capture"]["interval_seconds"] == 3
    assert configuration.current.capture.params == {"Brightness": 0.25}
    assert Configuration.load(path).current == configuration.current


@pytest.mark.covers("CFG-005", "CFG-009")
def test_d2_runtime_change_rejects_storage_and_camera(
    tmp_path: Path,
) -> None:
    configuration = make_configuration(tmp_path / "config.json")
    base = configuration.current.model_dump(mode="json")

    with pytest.raises(UnsupportedRuntimeChange, match="storage"):
        configuration.replace(
            {
                **base,
                "storages": [
                    {"type": "file", "name": "late", "directory": "./late"}
                ],
            },
            lambda _capture: None,
        )

    with pytest.raises(UnsupportedRuntimeChange, match="camera"):
        configuration.replace(
            {
                **base,
                "camera": {
                    "type": "v4l2",
                    "device": "/dev/video0",
                },
            },
            lambda _capture: None,
        )

    configured = Options.model_validate(
        {
            "capture": {"interval_seconds": 1},
            "storages": [
                {"type": "file", "name": "local", "directory": "captures"}
            ],
            "maintenance": {
                "retention": [{"target": "local", "max_age_days": 180}]
            },
        }
    )
    maintenance_configuration = Configuration(
        JsonOptionsRepository(tmp_path / "maintenance.json"),
        configured,
    )
    changed = configured.model_dump(mode="json")
    changed["maintenance"]["interval_seconds"] = 3600
    with pytest.raises(UnsupportedRuntimeChange, match="maintenance"):
        maintenance_configuration.replace(changed, lambda _capture: None)


def test_retention_targets_must_be_configured_and_unique() -> None:
    base = {
        "capture": {"interval_seconds": 1},
        "storages": [
            {"type": "file", "name": "local", "directory": "captures"}
        ],
    }

    with pytest.raises(ValidationError, match="not configured"):
        Options.model_validate(
            {
                **base,
                "maintenance": {
                    "retention": [{"target": "missing", "max_age_days": 180}]
                },
            }
        )
    with pytest.raises(ValidationError, match="must be unique"):
        Options.model_validate(
            {
                **base,
                "maintenance": {
                    "retention": [
                        {"target": "local", "max_age_days": 180},
                        {"target": "local", "max_age_days": 30},
                    ]
                },
            }
        )


def test_calibration_metadata_is_preserved_without_applying_image_correction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    configuration = make_configuration(path)
    document = configuration.current.model_dump(mode="json")
    document["calibration"] = {
        "type": "checkerboard",
        "camera_matrix": [[1.0, 0.0, 2.0], [0.0, 1.0, 3.0], [0.0, 0.0, 1.0]],
        "distortion_coefficients": [0.1, -0.2, 0.0, 0.0, 0.0],
    }

    result = configuration.replace(document, lambda _capture: None)

    assert result.succeeded
    assert result.options.calibration["type"] == "checkerboard"
    assert Configuration.load(path).current.calibration == result.options.calibration


@pytest.mark.covers("CFG-009", "CAP-008", "CAP-009")
def test_d2_runtime_change_persists_image_policy(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    configuration = make_configuration(path)
    base = configuration.current.model_dump(mode="json")

    result = configuration.replace(
        {
            **base,
            "image": {
                "format": "png",
                "compression": 7,
                "embed_metadata": False,
            },
        },
        lambda _capture: None,
    )

    assert result.succeeded
    assert result.options.image.format == "png"
    assert result.options.image.compression == 7
    assert result.options.image.embed_metadata is False
    assert Configuration.load(path).current.image == result.options.image


class FailingRepository(JsonOptionsRepository):
    def write(self, options: Options) -> None:
        raise OSError("disk is read-only")


@pytest.mark.covers("CFG-011")
def test_d3_write_failure_preserves_active_configuration(tmp_path: Path) -> None:
    original = Options.model_validate({"capture": {"interval_seconds": 2}})
    configuration = Configuration(
        FailingRepository(tmp_path / "config.json"),
        original,
    )

    result = configuration.replace(
        {"capture": {"interval_seconds": 4}},
        lambda _capture: pytest.fail("apply must follow a successful write"),
    )

    assert not result.succeeded
    assert isinstance(result.cause, OSError)
    assert configuration.current is original


@pytest.mark.covers("CFG-010", "CFG-011")
def test_d4_apply_failure_keeps_runtime_but_leaves_new_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    configuration = make_configuration(path)
    original = configuration.current

    def fail_apply(_capture) -> None:
        raise RuntimeError("camera rejected controls")

    result = configuration.replace(
        {"capture": {"interval_seconds": 9}},
        fail_apply,
    )

    assert not result.succeeded
    assert configuration.current is original
    assert Configuration.load(path).current.capture.interval_seconds == 9
