"""CaptureNode HTTP stream transport, independent of API authentication."""

import asyncio
from urllib.parse import urlsplit

IDLE_TIMEOUT_SECONDS = 30


async def stream_body(reader, writer, chunked):
    try:
        if chunked:
            async for chunk in chunked_body(reader):
                yield chunk
        else:
            while chunk := await asyncio.wait_for(
                reader.read(64 * 1024), timeout=IDLE_TIMEOUT_SECONDS
            ):
                yield chunk
    finally:
        writer.close()
        await writer.wait_closed()


async def open_preview_stream(
    endpoint: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str, bool]:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("CaptureNode preview endpoint must be a plain HTTP origin")
    port = parsed.port or 80
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(parsed.hostname, port), timeout=15
    )
    try:
        writer.write(
            (
                "GET /api/preview HTTP/1.1\r\n"
                f"Host: {parsed.hostname}:{port}\r\n"
                "Accept: multipart/x-mixed-replace\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
        media_type, chunked = parse_preview_headers(header)
        return reader, writer, media_type, chunked
    except BaseException:
        writer.close()
        await writer.wait_closed()
        raise


def parse_preview_headers(header: bytes) -> tuple[str, bool]:
    lines = header[:-4].split(b"\r\n")
    if not lines or not lines[0].startswith(b"HTTP/1.1 200 "):
        status = (
            lines[0].decode("ascii", errors="replace") if lines else "empty response"
        )
        raise OSError(f"CaptureNode preview returned {status}")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(b":")
        if separator:
            header_name = name.strip().decode("ascii", errors="ignore").lower()
            headers[header_name] = value.strip().decode("ascii", errors="replace")
    media_type = headers.get("content-type", "multipart/x-mixed-replace")
    if not media_type.lower().startswith("multipart/x-mixed-replace"):
        raise OSError("CaptureNode preview response is not an MJPEG stream")
    chunked = "chunked" in headers.get("transfer-encoding", "").lower()
    return media_type, chunked


async def chunked_body(reader: asyncio.StreamReader):
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=IDLE_TIMEOUT_SECONDS)
        if not line:
            raise OSError("CaptureNode preview ended before a chunk header")
        try:
            size = int(line.split(b";", 1)[0].strip(), 16)
        except ValueError as exc:
            raise OSError("CaptureNode preview has an invalid chunk header") from exc
        if size < 0 or size > 16 * 1024 * 1024:
            raise OSError("CaptureNode preview chunk is out of range")
        if size == 0:
            while trailer := await asyncio.wait_for(
                reader.readline(), timeout=IDLE_TIMEOUT_SECONDS
            ):
                if trailer == b"\r\n":
                    return
            return
        remaining = size
        while remaining:
            data = await asyncio.wait_for(
                reader.read(min(64 * 1024, remaining)), timeout=IDLE_TIMEOUT_SECONDS
            )
            if not data:
                raise OSError("CaptureNode preview ended inside a chunk")
            remaining -= len(data)
            yield data
        if (
            await asyncio.wait_for(reader.readexactly(2), timeout=IDLE_TIMEOUT_SECONDS)
            != b"\r\n"
        ):
            raise OSError("CaptureNode preview chunk is missing its trailing CRLF")
