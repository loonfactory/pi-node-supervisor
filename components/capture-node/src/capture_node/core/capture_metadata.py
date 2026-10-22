"""Snapshot geometric calibration and capture conditions without its provenance."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

from capture_node.core.cameras import CameraOptions, Capture
from capture_node.core.cameras.calibration import compact_calibration


def with_capture_metadata(
    capture: Capture,
    camera: CameraOptions,
    *,
    capture_id: str,
    request_id: str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Capture:
    """Attach the calibration and settings snapshot used for this exposure."""
    configured = {**camera.params, **(overrides or {})}
    parameters = {
        **capture.parameters,
        "captureId": capture_id,
        "captureSettings": {
            "imageWidth": capture.image.width,
            "imageHeight": capture.image.height,
            "horizontalFlip": configured.get("horizontal_flip", False),
            "verticalFlip": configured.get("vertical_flip", False),
            # Requested controls are separate from observed LensPosition etc.
            "configuredControls": deepcopy(configured),
        },
    }
    if request_id is not None:
        parameters["requestId"] = request_id
    calibration = compact_calibration(camera.calibration)
    if calibration:
        parameters["calibration"] = calibration
    return replace(capture, parameters=parameters)
