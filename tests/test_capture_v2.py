from __future__ import annotations

import base64
import hashlib
import json
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

from pi_node_supervisor.capture_v2 import CaptureBundleError, CaptureNodeBundleClient


class Response(BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str]):
        super().__init__(body)
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name] = value


def response(parts, *, artifact_count=None):
    boundary = "capture"
    capture_id = "capture-001"
    body = bytearray()
    for name, media_type, data in parts:
        digest = base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f"Content-Type: {media_type}\r\n".encode())
        body.extend(f'Content-Disposition: attachment; filename="{name}"\r\n'.encode())
        body.extend(f"Content-Length: {len(data)}\r\n".encode())
        body.extend(f"Digest: sha-256={digest}\r\n".encode())
        body.extend(f"X-Capture-Id: {capture_id}\r\n\r\n".encode())
        body.extend(data)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return Response(
        bytes(body),
        {
            "Content-Type": f"multipart/mixed; boundary={boundary}",
            "X-Request-Id": "run-001",
            "X-Capture-Id": capture_id,
            "X-Captured-At": "2030-01-01T12:00:00+09:00",
            "X-Artifact-Count": str(
                len(parts) if artifact_count is None else artifact_count
            ),
        },
    )


def test_capture_bundle_is_verified_and_staged(tmp_path: Path) -> None:
    upstream = response(
        [("capture.dng", "image/x-adobe-dng", b"raw-image" * 1000)]
    )
    client = CaptureNodeBundleClient(
        "http://127.0.0.1:8000", tmp_path, opener=lambda *_a, **_kw: upstream
    )

    bundle = client.capture("run-001", parameters={"ExposureTime": 12000})

    assert bundle.capture_id == "capture-001"
    assert bundle.artifacts[0].path.read_bytes() == b"raw-image" * 1000
    directory = bundle.directory
    bundle.close()
    assert not directory.exists()


def test_invalid_bundle_is_removed_from_staging(tmp_path: Path) -> None:
    upstream = response([("capture.dng", "image/x-adobe-dng", b"raw")], artifact_count=2)
    client = CaptureNodeBundleClient(
        "http://127.0.0.1:8000", tmp_path, opener=lambda *_a, **_kw: upstream
    )

    with pytest.raises(CaptureBundleError, match="artifact count mismatch"):
        client.capture("run-001")

    assert list(tmp_path.iterdir()) == []


def test_capture_target_and_sensor_timing_cross_http_boundary(tmp_path):
    upstream = response([("image.dng", "image/x-adobe-dng", b"raw")])
    upstream.headers["X-Sensor-Timestamp-Ns"] = "12345678901234567"
    upstream.headers["X-Sync-Ready"] = "1"
    requests = []

    def open_response(request, **kwargs):
        requests.append(json.loads(request.data))
        return upstream

    client = CaptureNodeBundleClient("http://localhost:8000", tmp_path, opener=open_response)
    with client.capture("run-001", capture_at_ns=12345678901234000) as bundle:
        assert bundle.sensor_timestamp_ns == 12345678901234567
        assert bundle.sync_ready is True
    assert requests == [{"requestId": "run-001", "captureAtNs": 12345678901234000}]


@pytest.mark.parametrize("timestamp,ready", [("bad", "1"), ("-1", "1"), ("1", "true")])
def test_invalid_sensor_timing_rejected_before_staging(tmp_path, timestamp, ready):
    upstream = response([("image.dng", "image/x-adobe-dng", b"raw")])
    upstream.headers["X-Sensor-Timestamp-Ns"] = timestamp
    upstream.headers["X-Sync-Ready"] = ready
    client = CaptureNodeBundleClient("http://localhost", tmp_path, opener=lambda *a, **k: upstream)
    with pytest.raises(CaptureBundleError, match="timing headers"):
        client.capture("run-001")
    assert not list(tmp_path.iterdir())
