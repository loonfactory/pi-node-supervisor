"""Validated image encoding policy."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from capture_node.core.models import StrictModel


class ImageOptions(StrictModel):
    format: Literal["jpeg", "png"] = "jpeg"
    quality: int = Field(default=90, ge=1, le=100)
    embed_metadata: bool = True
