"""PNG image encoding."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from capture_node.core.cameras import Capture
from capture_node.core.encodings.contracts import EncodedImage
from capture_node.core.encodings.exif import capture_exif
from capture_node.core.encodings.options import ImageOptions, PngImageOptions


class PngEncoding:
    format = "png"
    extension = "png"

    def encode(self, capture: Capture, options: ImageOptions) -> EncodedImage:
        if not isinstance(options, PngImageOptions):
            raise TypeError("PNG encoding requires PngImageOptions")
        output = BytesIO()
        save_options: dict[str, Any] = {
            "format": "PNG",
            "compress_level": options.compression,
        }
        if options.embed_metadata:
            save_options["exif"] = capture_exif(capture)
        capture.image.save(output, **save_options)
        return EncodedImage(output.getvalue(), self.extension)
