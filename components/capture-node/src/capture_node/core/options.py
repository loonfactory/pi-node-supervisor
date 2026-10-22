"""Validated options for the complete capture application."""

from __future__ import annotations

from datetime import time as Time
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from capture_node.core.cameras.options import CameraOptions, Picamera2Options
from capture_node.core.encodings.options import ImageFormatsOptions, PngImageOptions
from capture_node.core.models import StrictModel
from capture_node.core.storages.options import StorageOptions


class DailyCaptureOptions(StrictModel):
    type: Literal["daily"]
    time: Time

    @field_validator("time")
    @classmethod
    def require_local_time(cls, value: Time) -> Time:
        if value.tzinfo is not None:
            raise ValueError("capture time must not include an offset")
        return value


class IntervalCaptureOptions(StrictModel):
    type: Literal["interval"]
    startTime: Time | None = None
    endTime: Time | None = None
    interval: float = Field(gt=0)

    @field_validator("startTime", "endTime")
    @classmethod
    def require_local_time(cls, value: Time | None) -> Time | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("capture time must not include an offset")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> IntervalCaptureOptions:
        if (self.startTime is None) != (self.endTime is None):
            raise ValueError("startTime and endTime must be configured together")
        if (
            self.startTime is not None
            and self.endTime is not None
            and self.endTime < self.startTime
        ):
            raise ValueError("endTime must not be earlier than startTime")
        return self

    @model_serializer
    def serialize_capture(self) -> dict[str, object]:
        document: dict[str, object] = {
            "type": self.type,
            "interval": self.interval,
        }
        if self.startTime is not None:
            assert self.endTime is not None
            document["startTime"] = self.startTime.isoformat()
            document["endTime"] = self.endTime.isoformat()
        return document


CaptureOptions = Annotated[
    DailyCaptureOptions | IntervalCaptureOptions,
    Field(discriminator="type"),
]


class Options(StrictModel):
    camera: CameraOptions = Field(default_factory=Picamera2Options)
    captures: tuple[CaptureOptions, ...] = ()
    images: ImageFormatsOptions = Field(
        default_factory=lambda: ImageFormatsOptions(png=PngImageOptions())
    )
    storages: tuple[StorageOptions, ...] = ()

    @model_validator(mode="after")
    def validate_storage_targets(self) -> Options:
        if len(set(self.captures)) != len(self.captures):
            raise ValueError("capture schedules must be unique")
        if self.images.dng is not None and self.camera.type != "picamera2":
            raise ValueError("DNG raw capture requires a picamera2 camera")
        storage_names = [storage.name for storage in self.storages]
        if len(set(storage_names)) != len(storage_names):
            raise ValueError("storage target names must be unique")
        return self
