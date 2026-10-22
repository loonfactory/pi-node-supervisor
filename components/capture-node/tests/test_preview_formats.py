from io import BytesIO

import pytest
from PIL import Image

from capture_node.core.cameras import PreviewFormat
from capture_node.core.cameras.preview_encoding import encode_preview
from capture_node.web.preview_negotiation import negotiate_preview
from capture_node.web.problems import ApiProblem


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        (None, PreviewFormat.PNG),
        ("*/*", PreviewFormat.PNG),
        ("multipart/x-mixed-replace", PreviewFormat.PNG),
        ("image/avif,image/webp,image/apng,image/*,*/*;q=0.8", PreviewFormat.PNG),
        ("image/jpeg", PreviewFormat.JPEG),
        ("image/png;q = 0.5,image/jpeg;q=1", PreviewFormat.JPEG),
        ("image/png;unsupported=1,*/*", PreviewFormat.PNG),
        ("image/png;q=0.8,image/jpeg;q=0.9", PreviewFormat.JPEG),
        ("image/jpeg;q=0.9,image/png;q=0.9", PreviewFormat.PNG),
        ("image/png;q=0,*/*;q=1", PreviewFormat.JPEG),
        ("image/*;q=0.8,image/jpeg;q=0.1", PreviewFormat.PNG),
    ],
)
def test_accept_selects_supported_frame_format(accept, expected):
    assert negotiate_preview(None, accept) == expected


@pytest.mark.parametrize(
    ("format", "accept", "status"),
    [
        ("webp", None, 422),
        ("", None, 422),
        (None, "image/webp", 406),
        (None, "image/png;q=0,image/jpeg;q=0,*/*;q=1", 406),
        (None, "*/*;q=0", 406),
        (None, "image/png;q=invalid", 400),
        (None, "image/png;q=1.1", 400),
    ],
)
def test_unsupported_or_invalid_preview_requests_fail(format, accept, status):
    with pytest.raises(ApiProblem) as error:
        negotiate_preview(format, accept)
    assert error.value.status == status


def test_explicit_format_overrides_accept():
    assert negotiate_preview("png", "image/jpeg") == PreviewFormat.PNG
    assert negotiate_preview("jpeg", "image/png") == PreviewFormat.JPEG


def test_png_preserves_every_pixel_and_dimensions():
    source = Image.frombytes(
        "RGB", (19, 13), bytes(i % 256 for i in range(19 * 13 * 3))
    )
    frame = encode_preview(source, PreviewFormat.PNG)
    assert frame.media_type == "image/png"
    with Image.open(BytesIO(frame.data)) as decoded:
        assert decoded.format == "PNG"
        assert decoded.size == source.size
        assert decoded.tobytes() == source.tobytes()


def test_jpeg_remains_available():
    frame = encode_preview(Image.new("RGB", (19, 13), "green"), PreviewFormat.JPEG)
    assert frame.media_type == "image/jpeg"
    with Image.open(BytesIO(frame.data)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (19, 13)
