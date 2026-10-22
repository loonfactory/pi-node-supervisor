from __future__ import annotations

import json
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
    example = repository_root / "config.example.json"

    options = Options.model_validate_json(example.read_text(encoding="utf-8"))

    assert options.captures == ()
    assert options.storages == ()


def test_picamera2_size_is_validated_by_its_options_model() -> None:
    options = Picamera2Options(cameraNum=1, width=1920, height=1080)

    assert options.camera_num == 1
    assert options.size == (1920, 1080)
    with pytest.raises(ValidationError, match="configured together"):
        Picamera2Options(width=1920)
    with pytest.raises(ValidationError):
        Picamera2Options(cameraNum=-1)


def test_dng_is_disabled_by_default_and_restricted_to_picamera2() -> None:
    options = Options.model_validate(
        {
            "captures": [{"type": "interval", "interval": 60}],
            "images": {"png": {"compression": 7}},
        }
    )
    assert options.images.dng is None

    document = options.model_dump(mode="json")
    document["camera"] = {
        "type": "v4l2",
        "device": "/dev/video0",
        "width": 1920,
        "height": 1080,
        "frameRate": 15,
        "startupTimeoutSeconds": 10,
    }
    document["images"]["dng"] = {"embedMetadata": True}
    with pytest.raises(ValueError, match="requires a picamera2"):
        Options.model_validate(document)


def test_image_formats_require_at_least_one_non_null_resource() -> None:
    for invalid_images in ({}, {"png": None}):
        with pytest.raises(ValidationError, match="image format"):
            Options.model_validate(
                {
                    "captures": [{"type": "interval", "interval": 60}],
                    "images": invalid_images,
                }
            )


@pytest.mark.covers("CFG-001", "CFG-002", "CFG-003", "CFG-004", "CFG-008")
def test_a1_loads_full_headless_configuration_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"captures": [{"type": "interval", "interval": 5}]}),
        encoding="utf-8",
    )

    configuration = Configuration.load(path)

    assert configuration.current.captures[0].interval == 5
    assert configuration.current.camera.type == "picamera2"
    assert configuration.current.images.png is not None
    assert configuration.current.images.png.compression == 7
    assert configuration.current.images.png.embed_metadata is True
    assert configuration.current.storages == ()


@pytest.mark.parametrize(
    "document",
    [
        {"captures": [{"type": "interval", "interval": 0}]},
        {"captures": [{"type": "interval", "interval": 1, "unknown": True}]},
        {"images": {}},
        {
            "captures": [{"type": "interval", "interval": 1}],
            "images": {"png": {"quality": 90}},
        },
        {"captures": [], "unknown": True},
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
def test_daily_capture_rules_are_strictly_validated() -> None:
    options = Options.model_validate(
        {
            "captures": [
                {"type": "daily", "time": "08:30:00"},
                {
                    "type": "interval",
                    "startTime": "12:00:00",
                    "endTime": "23:00:00",
                    "interval": 60,
                },
                {"type": "interval", "interval": 60},
            ]
        }
    )

    assert options.model_dump(mode="json")["captures"] == [
        {"type": "daily", "time": "08:30:00"},
        {
            "type": "interval",
            "interval": 60.0,
            "startTime": "12:00:00",
            "endTime": "23:00:00",
        },
        {"type": "interval", "interval": 60.0},
    ]

    invalid_documents = [
        {
            "captures": [
                {"type": "interval", "startTime": "08:30:00", "interval": 10}
            ]
        },
        {
            "captures": [
                {
                    "type": "interval",
                    "startTime": "18:00:00",
                    "endTime": "08:00:00",
                    "interval": 10,
                }
            ]
        },
        {
            "captures": [
                {"type": "daily", "time": "08:30:00"},
                {"type": "daily", "time": "08:30:00"},
            ]
        },
        {
            "captures": [{"type": "daily", "time": "08:30:00+09:00"}]
        },
        {
            "captures": [
                {"type": "daily", "time": "08:30:00", "date": "2026-08-24"}
            ]
        },
    ]
    for document in invalid_documents:
        with pytest.raises(ValidationError):
            Options.model_validate(document)


@pytest.mark.parametrize(
    "legacy_property",
    [
        {
            "capture": {
                "mode": "daily",
                "intervalSeconds": None,
                "daily_times": ["08:30:00"],
                "timezone": "Asia/Seoul",
            }
        },
        {
            "capture": {"intervalSeconds": 60},
            "image": {
                "format": "png",
                "compression": 7,
                "embedMetadata": True,
                "raw": {"enabled": True, "format": "dng"},
            },
        },
    ],
)
def test_loading_configuration_rejects_legacy_schema_without_rewriting(
    tmp_path: Path,
    legacy_property: dict,
) -> None:
    path = tmp_path / "config.json"
    original = json.dumps(legacy_property)
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValidationError):
        Configuration.load(path)

    assert path.read_text(encoding="utf-8") == original


@pytest.mark.covers("CAP-001", "CFG-001", "CFG-005")
def test_empty_capture_rules_have_no_automatic_triggers() -> None:
    assert Options.model_validate({"captures": []}).captures == ()


@pytest.mark.covers("CFG-009", "CFG-010")
def test_d1_valid_runtime_change_is_persisted_before_application(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    configuration = make_configuration(path)
    observed_file: list[dict] = []

    def apply(_options) -> None:
        observed_file.append(json.loads(path.read_text(encoding="utf-8")))

    result = configuration.replace(
        {
            **configuration.current.model_dump(mode="json"),
            "captures": [{"type": "interval", "interval": 3}],
            "camera": {
                **configuration.current.camera.model_dump(mode="json"),
                "params": {"Brightness": 0.25},
            },
        },
        apply,
    )

    assert result.succeeded
    assert observed_file[0]["captures"][0]["interval"] == 3
    assert configuration.current.camera.params == {"Brightness": 0.25}
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
            "captures": [{"type": "interval", "interval": 1}],
            "storages": [
                {
                    "type": "file",
                    "name": "local",
                    "directory": "captures",
                    "maintenance": {
                        "intervalSeconds": 86400,
                        "maxAgeDays": 180,
                    },
                }
            ],
        }
    )
    maintenance_configuration = Configuration(
        JsonOptionsRepository(tmp_path / "maintenance.json"),
        configured,
    )
    changed = configured.model_dump(mode="json")
    changed["storages"][0]["maintenance"]["intervalSeconds"] = 3600
    with pytest.raises(UnsupportedRuntimeChange, match="storage"):
        maintenance_configuration.replace(changed, lambda _capture: None)


def test_storage_maintenance_is_validated_per_storage() -> None:
    options = Options.model_validate(
        {
            "captures": [{"type": "interval", "interval": 1}],
            "storages": [
                {
                    "type": "file",
                    "name": "local",
                    "directory": "captures",
                    "maintenance": {"maxAgeDays": 180},
                },
                {
                    "type": "file",
                    "name": "archive",
                    "directory": "archive",
                    "maintenance": {
                        "intervalSeconds": 3600,
                        "maxAgeDays": 365,
                    },
                },
            ],
        }
    )

    assert options.storages[0].maintenance is not None
    assert options.storages[0].maintenance.interval_seconds == 86400
    assert options.storages[1].maintenance is not None
    assert options.storages[1].maintenance.max_age_days == 365

    for invalid in (
        {"intervalSeconds": 0, "maxAgeDays": 7},
        {"intervalSeconds": 60, "maxAgeDays": 0},
    ):
        with pytest.raises(ValidationError):
            Options.model_validate(
                {
                    "captures": [{"type": "interval", "interval": 1}],
                    "storages": [
                        {
                            "type": "file",
                            "name": "local",
                            "directory": "captures",
                            "maintenance": invalid,
                        }
                    ],
                }
            )


def test_calibration_metadata_is_preserved_without_applying_image_correction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    configuration = make_configuration(path)
    document = configuration.current.model_dump(mode="json")
    document["camera"]["calibration"] = {
        "type": "checkerboard",
        "camera_matrix": [[1.0, 0.0, 2.0], [0.0, 1.0, 3.0], [0.0, 0.0, 1.0]],
        "distortion_coefficients": [0.1, -0.2, 0.0, 0.0, 0.0],
    }

    result = configuration.replace(document, lambda _capture: None)

    assert result.succeeded
    assert result.options.camera.calibration["type"] == "checkerboard"
    assert (
        Configuration.load(path).current.camera.calibration
        == result.options.camera.calibration
    )


@pytest.mark.covers("CFG-009", "CAP-008", "CAP-009")
def test_d2_runtime_change_persists_image_policy(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    configuration = make_configuration(path)
    base = configuration.current.model_dump(mode="json")

    result = configuration.replace(
        {
            **base,
            "images": {
                "png": {
                    "compression": 7,
                    "embedMetadata": False,
                },
            },
        },
        lambda _capture: None,
    )

    assert result.succeeded
    assert result.options.images.png is not None
    assert result.options.images.png.compression == 7
    assert result.options.images.png.embed_metadata is False
    assert Configuration.load(path).current.images == result.options.images


class FailingRepository(JsonOptionsRepository):
    def write(self, options: Options) -> None:
        raise OSError("disk is read-only")


@pytest.mark.covers("CFG-011")
def test_d3_write_failure_preserves_active_configuration(tmp_path: Path) -> None:
    original = Options.model_validate(
        {"captures": [{"type": "interval", "interval": 2}]}
    )
    configuration = Configuration(
        FailingRepository(tmp_path / "config.json"),
        original,
    )

    result = configuration.replace(
        {"captures": [{"type": "interval", "interval": 4}]},
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
        {"captures": [{"type": "interval", "interval": 9}]},
        fail_apply,
    )

    assert not result.succeeded
    assert configuration.current is original
    assert Configuration.load(path).current.captures[0].interval == 9
