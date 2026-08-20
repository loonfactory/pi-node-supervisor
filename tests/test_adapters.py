from __future__ import annotations

from io import BytesIO
from datetime import UTC, datetime

import pytest

from pi_node_supervisor.adapters import AdapterError, CaptureNodeClient


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, *, timeout, document=None):
        self.calls.append((method, url, timeout, document))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)


def test_capture_node_client_uses_documented_capture_contract():
    transport = FakeTransport(
        [
            {"status": "running", "camera_connected": True, "camera_detail": None},
            {"capture_id": "cap-1", "captured_at": "2030-01-01T00:00:00Z"},
            {
                "items": [
                    {
                        "event_type": "terminal.completed",
                        "capture_id": "cap-1",
                        "targets": ["local"],
                    }
                ]
            },
        ]
    )
    client = CaptureNodeClient("http://127.0.0.1:8000", transport=transport)

    assert client.health()["camera_connected"] is True
    capture = client.capture()
    assert client.wait_for_terminal(capture["capture_id"])["targets"] == ["local"]
    assert [(method, url.split("8000", 1)[1]) for method, url, _, _ in transport.calls] == [
        ("GET", "/api/health"),
        ("POST", "/api/capture"),
        ("GET", "/api/events?page=1&page_size=50"),
    ]


def test_capture_parameters_are_applied_through_config_contract():
    current = {
        "camera": {"type": "picamera2"},
        "capture": {"mode": "disabled", "params": {}},
        "image": {"format": "jpeg"},
        "storages": [],
        "maintenance": {"retention": []},
    }
    transport = FakeTransport([current, {**current, "capture": {"mode": "disabled", "params": {"ExposureTime": 12000}}}])
    client = CaptureNodeClient("http://127.0.0.1:8000", transport=transport)
    response = client.configure_capture({"ExposureTime": 12000})
    assert response["capture"]["params"] == {"ExposureTime": 12000}
    method, url, _timeout, sent = transport.calls[1]
    assert method == "PUT"
    assert url.endswith("/api/config")
    assert sent["capture"]["mode"] == "disabled"
    assert sent["capture"]["params"] == {"ExposureTime": 12000}


def test_capture_node_client_rejects_unhealthy_camera():
    client = CaptureNodeClient(
        "http://127.0.0.1:8000",
        transport=FakeTransport(
            [{"status": "running", "camera_connected": False, "camera_detail": "offline"}]
        ),
    )
    with pytest.raises(AdapterError, match="offline"):
        client.health()


def test_capture_node_client_reads_one_bounded_preview_frame():
    frame = b"\xff\xd8preview\xff\xd9"

    class PreviewStream(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    calls = []

    def open_preview(request, *, timeout):
        calls.append((request.full_url, timeout, request.headers["Accept"]))
        return PreviewStream(
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
            + frame
            + b"\r\n"
        )

    client = CaptureNodeClient(
        "http://127.0.0.1:8000",
        request_timeout_seconds=3,
        preview_opener=open_preview,
    )
    assert client.preview_frame() == (frame, "image/jpeg")
    assert calls == [
        ("http://127.0.0.1:8000/api/preview", 3, "multipart/x-mixed-replace")
    ]


def test_capture_node_client_rejects_unbounded_preview_frame():
    class PreviewStream(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    client = CaptureNodeClient(
        "http://127.0.0.1:8000",
        preview_opener=lambda *_args, **_kwargs: PreviewStream(
            b"--frame\r\nContent-Type: image/jpeg\r\n\r\ncontent"
        ),
    )
    with pytest.raises(AdapterError, match="content length"):
        client.preview_frame()


def test_preview_byte_stream_reads_consecutive_complete_frames():
    from io import BytesIO

    from pi_node_supervisor.adapters import PreviewByteStream

    body = (
        b"--upstream\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\none\r\n"
        b"--upstream\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\ntwo\r\n"
    )
    stream = PreviewByteStream(
        BytesIO(body), "multipart/x-mixed-replace; boundary=upstream"
    )

    assert stream.read_frame() == (b"one", "image/jpeg")
    assert stream.read_frame() == (b"two", "image/jpeg")
    assert stream.read_frame() is None


def test_capture_node_client_proxies_and_closes_mjpeg_stream():
    class MjpegStream(BytesIO):
        headers = {"Content-Type": "multipart/x-mixed-replace; boundary=frame"}

    source = MjpegStream(b"--frame\r\nframe-data")
    client = CaptureNodeClient(
        "http://127.0.0.1:8000",
        preview_opener=lambda *_args, **_kwargs: source,
    )
    stream = client.open_preview_stream()
    assert stream.media_type == "multipart/x-mixed-replace; boundary=frame"
    assert b"".join(stream) == b"--frame\r\nframe-data"
    assert source.closed is True


def test_capture_node_data_catalog_keeps_remote_original_uri():
    transport = FakeTransport(
        [
            {"items": [{"name": "local"}]},
            {
                "items": [
                    {
                        "name": "20300101_cap-1.jpg",
                        "size": 1234,
                        "modified_at": "2030-01-01T00:00:05+00:00",
                    },
                    {
                        "name": "20300101_cap-1.dng",
                        "size": 4567,
                        "modified_at": "2030-01-01T00:00:05+00:00",
                    },
                    {
                        "name": "other.jpg",
                        "size": 9,
                        "modified_at": "2030-01-01T00:00:04+00:00",
                    },
                ]
            },
        ]
    )
    client = CaptureNodeClient("http://127.0.0.1:8000", transport=transport)
    records = client.data_records(
        node_id="camera0", activity_id="capture", run_id="run-1", capture_id="cap-1"
    )

    assert len(records) == 2
    assert records[0].data_id == "camera0:local:20300101_cap-1.jpg"
    assert records[0].uri == (
        "http://127.0.0.1:8000/api/storage/files/local/20300101_cap-1.jpg"
    )
    assert records[0].size == 1234
    assert records[0].captured_at == datetime(2030, 1, 1, 0, 0, 5, tzinfo=UTC)
    assert records[1].data_id == "camera0:local:20300101_cap-1.dng"
    assert records[1].media_type == "image/x-adobe-dng"
