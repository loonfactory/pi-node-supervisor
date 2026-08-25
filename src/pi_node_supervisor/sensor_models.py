"""Sensor values shared by the v2 driver and execution pipeline."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SensorReading(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: datetime
    temperature_c: float
    humidity_percent: float = Field(ge=0, le=100)

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observedAt must include a timezone")
        return value.astimezone(UTC)

    @field_validator("temperature_c", "humidity_percent")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("sensor values must be finite")
        return value
