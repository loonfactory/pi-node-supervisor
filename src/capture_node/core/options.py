"""Validated options for the complete capture application."""

from __future__ import annotations

from datetime import time as Time
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from capture_node.core.cameras.options import CameraOptions, Picamera2Options
from capture_node.core.encodings.options import ImageOptions, PngImageOptions
from capture_node.core.maintenance import MaintenanceOptions
from capture_node.core.models import StrictModel
from capture_node.core.storages.options import StorageOptions


class CaptureOptions(StrictModel):
    mode: Literal["interval", "daily", "disabled"] = "interval"
    interval_seconds: float | None = Field(default=None, gt=0)
    daily_times: tuple[Time, ...] = ()
    timezone: str = "Asia/Seoul"
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("daily_times", mode="before")
    @classmethod
    def normalize_missing_daily_times(cls, value: Any) -> Any:
        return () if value is None else value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def validate_schedule(self) -> CaptureOptions:
        if len(set(self.daily_times)) != len(self.daily_times):
            raise ValueError("daily capture times must be unique")
        if any(value.tzinfo is not None for value in self.daily_times):
            raise ValueError("daily capture times must not include an offset")
        if self.mode == "interval":
            if self.interval_seconds is None:
                raise ValueError("interval_seconds is required in interval mode")
            if self.daily_times:
                raise ValueError("daily_times must be empty in interval mode")
        elif self.mode == "daily":
            if self.interval_seconds is not None:
                raise ValueError("interval_seconds must be null in daily mode")
        else:
            if self.interval_seconds is not None:
                raise ValueError("interval_seconds must be null in disabled mode")
            if self.daily_times:
                raise ValueError("daily_times must be empty in disabled mode")
        return self


class Options(StrictModel):
    camera: CameraOptions = Field(default_factory=Picamera2Options)
    capture: CaptureOptions
    image: ImageOptions = Field(default_factory=PngImageOptions)
    storages: tuple[StorageOptions, ...] = ()
    maintenance: MaintenanceOptions = Field(default_factory=MaintenanceOptions)

    @model_validator(mode="after")
    def validate_storage_targets(self) -> Options:
        storage_names = [storage.name for storage in self.storages]
        if len(set(storage_names)) != len(storage_names):
            raise ValueError("storage target names must be unique")
        retention_targets = [rule.target for rule in self.maintenance.retention]
        if len(set(retention_targets)) != len(retention_targets):
            raise ValueError("retention targets must be unique")
        missing = set(retention_targets).difference(storage_names)
        if missing:
            raise ValueError(
                f"retention target is not configured: {sorted(missing)[0]}"
            )
        return self
