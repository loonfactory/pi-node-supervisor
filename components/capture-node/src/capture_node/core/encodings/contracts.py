"""Image encoding contracts shared by the capture pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from capture_node.core.cameras import Capture
from capture_node.core.encodings.options import ProcessedImageOptions


@dataclass(frozen=True, slots=True)
class EncodedImage:
    data: bytes
    extension: str


class ImageEncoding(Protocol):
    """Encodes captures into one registered image format."""

    @property
    def format(self) -> str:
        """Return the configuration key used to select this encoding."""

    @property
    def extension(self) -> str:
        """Return the filename extension produced by this encoding."""

    def encode(
        self,
        capture: Capture,
        options: ProcessedImageOptions,
    ) -> EncodedImage:
        """Encode one capture according to the shared image options."""
