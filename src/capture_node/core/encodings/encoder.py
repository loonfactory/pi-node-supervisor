"""Image encoding selection and delegation."""

from __future__ import annotations

from collections.abc import Sequence

from capture_node.core.cameras import Capture
from capture_node.core.encodings.contracts import EncodedImage, ImageEncoding
from capture_node.core.encodings.jpeg import JpegEncoding
from capture_node.core.encodings.options import ImageOptions
from capture_node.core.encodings.png import PngEncoding


class ImageEncoder:
    """Selects an image encoding without owning format-specific behavior."""

    def __init__(
        self,
        encodings: Sequence[ImageEncoding] | None = None,
    ) -> None:
        implementations = (
            (JpegEncoding(), PngEncoding())
            if encodings is None
            else encodings
        )
        self._encodings: dict[str, ImageEncoding] = {}
        for encoding in implementations:
            if not encoding.format:
                raise ValueError("image encoding format must not be empty")
            if encoding.format in self._encodings:
                raise ValueError(
                    f"image encoding already exists: {encoding.format}"
                )
            self._encodings[encoding.format] = encoding

    def encode(self, capture: Capture, options: ImageOptions) -> EncodedImage:
        try:
            encoding = self._encodings[options.format]
        except KeyError as exc:
            raise ValueError(
                f"unsupported image format: {options.format}"
            ) from exc
        return encoding.encode(capture, options)
