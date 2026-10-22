"""Attach capture JSON to a classic TIFF/DNG without rewriting sensor data."""

import json
import struct

from capture_node.core.cameras import Capture
from capture_node.core.encodings.options import DngImageOptions

_IMAGE_DESCRIPTION = 270
_DNG_VERSION = 50706
_IFD_ENTRY_SIZE = 12
_MAX_OFFSET = 2**32 - 1


def encode_dng(capture: Capture, options: DngImageOptions) -> bytes:
    """Preserve raw pixels and tags, adding JSON to IFD0 ImageDescription."""
    if capture.raw is None:
        raise ValueError("camera did not return the requested DNG raw capture")
    data = capture.raw.data
    if not options.embed_metadata:
        return data
    description = (
        json.dumps(
            {**capture.parameters, "capturedAt": capture.captured_at.isoformat()},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("ascii")
        + b"\x00"
    )
    return _with_description(data, description)


def _with_description(data: bytes, description: bytes) -> bytes:
    if len(data) < 8 or data[:4] not in (b"II*\x00", b"MM\x00*"):
        raise ValueError("DNG metadata requires a classic TIFF/DNG header")
    endian = "<" if data[:2] == b"II" else ">"
    ifd_offset = struct.unpack_from(endian + "I", data, 4)[0]
    if ifd_offset < 8 or ifd_offset + 2 > len(data):
        raise ValueError("DNG has an invalid first IFD offset")
    count = struct.unpack_from(endian + "H", data, ifd_offset)[0]
    entries_start = ifd_offset + 2
    entries_end = entries_start + count * _IFD_ENTRY_SIZE
    if entries_end + 4 > len(data):
        raise ValueError("DNG has a truncated first IFD")

    entries: dict[int, bytes] = {}
    for offset in range(entries_start, entries_end, _IFD_ENTRY_SIZE):
        tag = struct.unpack_from(endian + "H", data, offset)[0]
        if tag in entries:
            raise ValueError("DNG has duplicate first IFD tags")
        entries[tag] = data[offset : offset + _IFD_ENTRY_SIZE]
    if _DNG_VERSION not in entries:
        raise ValueError("raw artifact is missing the DNGVersion tag")

    # Relocate only IFD0. Original pixel blocks, SubIFDs and tag-value offsets
    # remain valid because the original byte positions are never moved.
    description_offset = (len(data) + 1) & ~1
    new_ifd_offset = (description_offset + len(description) + 1) & ~1
    entries[_IMAGE_DESCRIPTION] = struct.pack(
        endian + "HHII", _IMAGE_DESCRIPTION, 2, len(description), description_offset
    )
    end = new_ifd_offset + 2 + len(entries) * _IFD_ENTRY_SIZE + 4
    if len(entries) > 65535 or end > _MAX_OFFSET:
        raise ValueError("DNG metadata exceeds classic TIFF limits")
    output = bytearray(data)
    output.extend(b"\x00" * (description_offset - len(output)))
    output.extend(description)
    output.extend(b"\x00" * (new_ifd_offset - len(output)))
    output.extend(struct.pack(endian + "H", len(entries)))
    for tag in sorted(entries):
        output.extend(entries[tag])
    output.extend(data[entries_end : entries_end + 4])
    struct.pack_into(endian + "I", output, 4, new_ifd_offset)
    return bytes(output)
