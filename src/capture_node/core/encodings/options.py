"""Validated image encoding policy."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from capture_node.core.models import StrictModel


class JpegImageOptions(StrictModel):
    format: Literal["jpeg"] = "jpeg"
    quality: int = Field(default=90, ge=1, le=100)
    embed_metadata: bool = True


class PngImageOptions(StrictModel):
    format: Literal["png"] = "png"
    compression: int = Field(default=9, ge=0, le=9)
    embed_metadata: bool = True


ImageOptions = Annotated[
    JpegImageOptions | PngImageOptions,
    Field(discriminator="format"),
]
