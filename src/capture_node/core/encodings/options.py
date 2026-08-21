"""Validated image encoding policy."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from capture_node.core.models import StrictModel


class RawImageOptions(StrictModel):
    enabled: bool = False
    format: Literal["dng"] = "dng"


class JpegImageOptions(StrictModel):
    format: Literal["jpeg"] = "jpeg"
    quality: int = Field(default=90, ge=1, le=100)
    embed_metadata: bool = True
    raw: RawImageOptions = Field(default_factory=RawImageOptions)


class PngImageOptions(StrictModel):
    format: Literal["png"] = "png"
    compression: int = Field(default=7, ge=0, le=9)
    embed_metadata: bool = True
    raw: RawImageOptions = Field(default_factory=RawImageOptions)


ImageOptions = Annotated[
    JpegImageOptions | PngImageOptions,
    Field(discriminator="format"),
]
