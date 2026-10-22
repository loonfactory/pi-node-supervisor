"""Validated image format policies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Literal

from pydantic import Field, model_serializer, model_validator

from capture_node.core.models import StrictModel


class JpegImageOptions(StrictModel):
    format: ClassVar[Literal["jpeg"]] = "jpeg"
    quality: int = Field(default=90, ge=1, le=100)
    embed_metadata: bool = True


class PngImageOptions(StrictModel):
    format: ClassVar[Literal["png"]] = "png"
    compression: int = Field(default=7, ge=0, le=9)
    embed_metadata: bool = True


class DngImageOptions(StrictModel):
    format: ClassVar[Literal["dng"]] = "dng"
    embed_metadata: bool = True


class ImageFormatsOptions(StrictModel):
    """Enabled image formats keyed by their filename extension."""

    png: PngImageOptions | None = None
    jpeg: JpegImageOptions | None = None
    dng: DngImageOptions | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_formats(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            for image_format in ("png", "jpeg", "dng"):
                if image_format in value and value[image_format] is None:
                    raise ValueError(
                        f"image format {image_format!r} must be an object"
                    )
        return value

    @model_validator(mode="after")
    def require_one_format(self) -> ImageFormatsOptions:
        if not self.enabled:
            raise ValueError("at least one image format is required")
        return self

    @property
    def enabled(self) -> tuple[ProcessedImageOptions | DngImageOptions, ...]:
        return tuple(
            options
            for options in (self.png, self.jpeg, self.dng)
            if options is not None
        )

    @property
    def processed(self) -> tuple[ProcessedImageOptions, ...]:
        return tuple(
            options
            for options in (self.png, self.jpeg)
            if options is not None
        )

    @model_serializer
    def serialize_enabled_formats(self) -> dict[str, dict[str, object]]:
        return {
            options.format: options.model_dump(mode="json")
            for options in self.enabled
        }


ProcessedImageOptions = JpegImageOptions | PngImageOptions
