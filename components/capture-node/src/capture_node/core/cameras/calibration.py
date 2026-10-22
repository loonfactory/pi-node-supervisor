"""Compact per-camera pinhole calibration metadata."""

from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from typing import Any

_CALIBRATION_FIELDS = frozenset(
    {
        "image_width",
        "image_height",
        "camera_matrix",
        "distortion_coefficients",
        "R",
        "T_mm",
    }
)
_DISTORTION_NAMES = ("k1", "k2", "p1", "p2", "k3")


def compact_calibration(document: Mapping[str, Any]) -> dict[str, Any]:
    """Keep this camera's pinhole calibration, using named distortion terms."""
    if document.get("model", "opencv_pinhole") != "opencv_pinhole":
        raise ValueError("calibration metadata supports the OpenCV pinhole model")
    calibration = {
        name: deepcopy(value)
        for name, value in document.items()
        if name in _CALIBRATION_FIELDS
    }
    coefficients = calibration.get("distortion_coefficients")
    if "distortion_coefficients" not in calibration:
        return calibration
    if isinstance(coefficients, list):
        order = document.get("distortion_coefficient_order", _DISTORTION_NAMES)
        if (
            not isinstance(order, (list, tuple))
            or len(order) != 5
            or set(order) != set(_DISTORTION_NAMES)
        ):
            raise ValueError("distortion order must name k1, k2, p1, p2 and k3 once")
        coefficients = dict(zip(order, coefficients, strict=True))
    if not isinstance(coefficients, dict) or set(coefficients) != set(
        _DISTORTION_NAMES
    ):
        raise ValueError("distortion coefficients must contain k1, k2, p1, p2 and k3")
    if any(
        type(value) not in (int, float) or not isfinite(value)
        for value in coefficients.values()
    ):
        raise ValueError("distortion coefficients must be finite numbers")
    calibration["distortion_coefficients"] = {
        name: coefficients[name] for name in _DISTORTION_NAMES
    }
    return calibration
