"""Encode decoded preview pixels without resizing or altering camera controls."""

from io import BytesIO

from PIL import Image

from capture_node.core.cameras.contracts import PreviewFormat, PreviewFrame


def encode_preview(
    image: Image.Image, format: PreviewFormat, *, jpeg_quality: int = 75
) -> PreviewFrame:
    output = BytesIO()
    if format == PreviewFormat.PNG:
        # A low compression level reduces CPU cost without discarding pixels.
        image.save(output, format="PNG", compress_level=1)
    elif format == PreviewFormat.JPEG:
        image.save(output, format="JPEG", quality=jpeg_quality)
    else:
        raise ValueError(f"unsupported preview format: {format}")
    return PreviewFrame(output.getvalue(), f"image/{format.value}")
