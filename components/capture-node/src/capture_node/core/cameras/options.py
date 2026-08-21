"""Validated camera configuration models."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from capture_node.core.models import StrictModel


class Picamera2Options(StrictModel):
    type: Literal["picamera2"] = "picamera2"
    camera_num: int = Field(default=0, ge=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    preview_fps: float = Field(default=10, gt=0)
    preview_quality: int = Field(default=75, ge=1, le=100)

    @model_validator(mode="after")
    def validate_size(self) -> Picamera2Options:
        if (self.width is None) != (self.height is None):
            raise ValueError("picamera2 width and height must be configured together")
        return self

    @property
    def size(self) -> tuple[int, int] | None:
        if self.width is None:
            return None
        assert self.height is not None
        return self.width, self.height


class V4L2CameraOptions(StrictModel):
    type: Literal["v4l2"]
    device: Path = Path("/dev/video0")
    width: int = Field(default=1920, gt=0)
    height: int = Field(default=1080, gt=0)
    frame_rate: float = Field(default=24, gt=0)
    startup_timeout_seconds: float = Field(default=10, gt=0)


CameraOptions = Annotated[
    Picamera2Options | V4L2CameraOptions,
    Field(discriminator="type"),
]
