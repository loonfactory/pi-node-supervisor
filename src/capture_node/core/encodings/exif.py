"""EXIF metadata shared by image encoding implementations."""

from __future__ import annotations

import json

from PIL import ExifTags, Image

from capture_node.core.cameras import Capture


def capture_exif(capture: Capture) -> Image.Exif:
    exif = Image.Exif()
    exif[ExifTags.Base.DateTimeOriginal] = capture.captured_at.isoformat()
    exif[ExifTags.Base.UserComment] = b"ASCII\x00\x00\x00" + json.dumps(
        dict(capture.parameters),
        sort_keys=True,
        default=str,
    ).encode("utf-8")

    exposure = capture.parameters.get("ExposureTime")
    if exposure is not None:
        exif[ExifTags.Base.ExposureTime] = float(exposure) / 1_000_000

    gain = capture.parameters.get("AnalogueGain")
    if gain is not None:
        exif[ExifTags.Base.ISOSpeedRatings] = max(1, round(float(gain) * 100))
    return exif
