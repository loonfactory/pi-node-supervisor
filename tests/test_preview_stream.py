import asyncio

import pytest

from pi_node_supervisor import preview_stream


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
