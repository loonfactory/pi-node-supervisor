import asyncio

import pytest

from pi_node_supervisor import preview_stream


@pytest.mark.parametrize("format", [None, "png", "jpeg", "png&unexpected=value"])
def test_preview_transport_forwards_format_and_accept(format):
    async def exercise():
        received = asyncio.get_running_loop().create_future()

        async def serve(reader, writer):
            try:
                received.set_result(await reader.readuntil(b"\r\n\r\n"))
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            _, writer, media_type, chunked = await preview_stream.open_preview_stream(
                f"http://127.0.0.1:{port}",
                format=format,
                accept="image/png;q=0.8,image/jpeg",
            )
            writer.close()
            await writer.wait_closed()
            request = await received
        assert b"Accept: image/png;q=0.8,image/jpeg\r\n" in request
        from urllib.parse import parse_qs, urlsplit

        target = request.split(b" ")[1].decode()
        assert parse_qs(urlsplit(target).query) == (
            {"format": [format]} if format else {}
        )
        assert media_type == "multipart/x-mixed-replace; boundary=frame"
        assert chunked is False

    asyncio.run(exercise())


@pytest.mark.parametrize("status", [400, 406, 422])
def test_preview_transport_preserves_negotiation_rejections(status):
    with pytest.raises(preview_stream.PreviewRequestRejected) as error:
        preview_stream.parse_preview_headers(
            f"HTTP/1.1 {status} Rejected\r\n\r\n".encode()
        )
    assert error.value.status_code == status


def test_preview_transport_rejects_header_injection_before_connecting():
    with pytest.raises(preview_stream.PreviewRequestRejected) as error:
        asyncio.run(
            preview_stream.open_preview_stream(
                "http://127.0.0.1:1", accept="image/png\r\nInjected: value"
            )
        )
    assert error.value.status_code == 400


@pytest.mark.parametrize("chunked", [False, True])
def test_idle_stream_times_out_and_closes_upstream(monkeypatch, chunked):
    monkeypatch.setattr(preview_stream, "IDLE_TIMEOUT_SECONDS", 0.01)

    async def exercise():
        reader = asyncio.StreamReader()
        closed = []

        class Writer:
            def close(self):
                closed.append(True)

            async def wait_closed(self):
                pass

        with pytest.raises(TimeoutError):
            async for _ in preview_stream.stream_body(reader, Writer(), chunked):
                pass
        assert closed == [True]

    asyncio.run(exercise())


def test_partial_chunk_body_timeout_closes_upstream(monkeypatch):
    monkeypatch.setattr(preview_stream, "IDLE_TIMEOUT_SECONDS", 0.01)

    async def exercise():
        reader = asyncio.StreamReader()
        reader.feed_data(b"4\r\nab")
        closed = []

        class Writer:
            def close(self):
                closed.append(True)

            async def wait_closed(self):
                pass

        received = []
        with pytest.raises(TimeoutError):
            async for data in preview_stream.stream_body(reader, Writer(), True):
                received.append(data)
        assert received == [b"ab"]
        assert closed == [True]

    asyncio.run(exercise())
