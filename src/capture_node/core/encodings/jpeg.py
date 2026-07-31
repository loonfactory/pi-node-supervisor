"""JPEG image encoding."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from capture_node.core.cameras import Capture
from capture_node.core.encodings.contracts import EncodedImage
from capture_node.core.encodings.exif import capture_exif
from capture_node.core.encodings.options import ImageOptions


class JpegEncoding:
    format = "jpeg"
    extension = "jpg"

    def encode(self, capture: Capture, options: ImageOptions) -> EncodedImage:
        output = BytesIO()
        save_options: dict[str, Any] = {
            "format": "JPEG",
            "quality": options.quality,
        }
        if options.embed_metadata:
            save_options["exif"] = capture_exif(capture)
        capture.image.convert("RGB").save(output, **save_options)
        return EncodedImage(output.getvalue(), self.extension)
