"""Validated storage-maintenance policy options."""

from __future__ import annotations

from pydantic import Field

from capture_node.core.models import StrictModel


class RetentionOptions(StrictModel):
    target: str = Field(min_length=1)
    max_age_days: int = Field(gt=0)


class MaintenanceOptions(StrictModel):
    interval_seconds: float = Field(default=86400, gt=0)
    retention: tuple[RetentionOptions, ...] = ()
