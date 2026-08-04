"""Image encoding contracts, implementations, and lookup."""

from capture_node.core.encodings.contracts import EncodedImage, ImageEncoding
from capture_node.core.encodings.encoder import ImageEncoder
from capture_node.core.encodings.jpeg import JpegEncoding
from capture_node.core.encodings.options import (
    ImageOptions,
    JpegImageOptions,
    PngImageOptions,
)
from capture_node.core.encodings.png import PngEncoding

__all__ = [
    "EncodedImage",
    "ImageEncoder",
    "ImageEncoding",
    "ImageOptions",
    "JpegImageOptions",
    "JpegEncoding",
    "PngEncoding",
    "PngImageOptions",
]
