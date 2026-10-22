"""Streaming client for Supervisor-owned CaptureNode capture bundles."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Any, BinaryIO, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import SupervisorError


class CaptureBundleError(SupervisorError):
    pass


@dataclass(frozen=True)
class StagedArtifact:
    name: str
    media_type: str
    path: Path
    size: int
    sha256: str


class StagedCaptureBundle:
    def __init__(
        self,
        *,
        request_id: str,
        capture_id: str,
        captured_at: datetime,
        directory: Path,
        artifacts: tuple[StagedArtifact, ...],
        sensor_timestamp_ns: int | None = None,
        sync_ready: bool | None = None,
    ) -> None:
        self.request_id = request_id
        self.capture_id = capture_id
        self.captured_at = captured_at
        self.directory = directory
        self.artifacts = artifacts
        self.sensor_timestamp_ns = sensor_timestamp_ns
        self.sync_ready = sync_ready
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            shutil.rmtree(self.directory, ignore_errors=True)

    def __enter__(self) -> StagedCaptureBundle:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class CaptureNodeBundleClient:
    def __init__(
        self,
        endpoint: str,
        staging_root: Path,
        *,
        request_timeout_seconds: float = 10,
        capture_timeout_seconds: float = 300,
        opener: Callable[..., BinaryIO] = urlopen,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.staging_root = staging_root
        self.request_timeout_seconds = request_timeout_seconds
        self.capture_timeout_seconds = capture_timeout_seconds
        self.opener = opener

    def health(self) -> dict[str, Any]:
        request = Request(
            f"{self.endpoint}/api/health",
            method="GET",
            headers={"Accept": "application/json"},
        )
        try:
            with self.opener(request, timeout=self.request_timeout_seconds) as response:
                document = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise CaptureBundleError(f"CaptureNode health request failed: {exc}") from exc
        if document.get("status") != "running" or document.get("cameraConnected") is not True:
            raise CaptureBundleError(
                f"CaptureNode camera is unavailable: {document.get('cameraDetail') or 'unknown'}"
            )
        return document

    def capture(
        self,
        request_id: str,
        *,
        parameters: dict[str, Any] | None = None,
        formats: dict[str, Any] | None = None,
        capture_at_ns: int | None = None,
    ) -> StagedCaptureBundle:
        document: dict[str, Any] = {"requestId": request_id}
        if capture_at_ns is not None:
            document["captureAtNs"] = capture_at_ns
        if parameters:
            document["parameters"] = parameters
        if formats is not None:
            document["formats"] = formats
        request = Request(
            f"{self.endpoint}/api/captures",
            data=json.dumps(document, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Accept": "multipart/mixed", "Content-Type": "application/json"},
        )
        try:
            response = self.opener(request, timeout=self.capture_timeout_seconds)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise CaptureBundleError(
                f"CaptureNode returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise CaptureBundleError(f"CaptureNode capture request failed: {exc}") from exc
        try:
            return self._stage_response(response, request_id)
        finally:
            response.close()

    def _stage_response(
        self, response: BinaryIO, expected_request_id: str
    ) -> StagedCaptureBundle:
        headers = response.headers
        request_id = headers.get("X-Request-Id")
        capture_id = headers.get("X-Capture-Id")
        captured_at_value = headers.get("X-Captured-At")
        count_value = headers.get("X-Artifact-Count")
        if request_id != expected_request_id:
            raise CaptureBundleError("CaptureNode response request ID does not match")
        if not capture_id or not captured_at_value or not count_value:
            raise CaptureBundleError("CaptureNode response is missing bundle headers")
        try:
            captured_at = datetime.fromisoformat(captured_at_value)
            expected_count = int(count_value)
        except (ValueError, TypeError) as exc:
            raise CaptureBundleError("CaptureNode response has invalid bundle headers") from exc
        if captured_at.tzinfo is None or expected_count < 1:
            raise CaptureBundleError("CaptureNode response has invalid bundle metadata")
        boundary = _multipart_boundary(headers.get("Content-Type", ""))
        timestamp_value = headers.get("X-Sensor-Timestamp-Ns")
        ready_value = headers.get("X-Sync-Ready")
        try:
            timestamp = int(timestamp_value) if timestamp_value is not None else None
            if timestamp is not None and timestamp <= 0:
                raise ValueError("invalid sensor timestamp")
            if ready_value not in (None, "0", "1"):
                raise ValueError("invalid sync readiness")
        except ValueError as exc:
            raise CaptureBundleError("invalid capture timing headers") from exc
        directory = Path(
            tempfile.mkdtemp(prefix=f"capture-{request_id}-", dir=self._staging_directory())
        )
        try:
            artifacts = self._read_parts(response, boundary, capture_id, directory)
            if len(artifacts) != expected_count:
                raise CaptureBundleError(
                    f"artifact count mismatch: expected {expected_count}, received {len(artifacts)}"
                )
            return StagedCaptureBundle(
                request_id=request_id,
                capture_id=capture_id,
                captured_at=captured_at,
                directory=directory,
                artifacts=tuple(artifacts),
                sensor_timestamp_ns=timestamp,
                sync_ready=None if ready_value is None else ready_value == "1",
            )
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def _read_parts(
        self,
        stream: BinaryIO,
        boundary: bytes,
        capture_id: str,
        directory: Path,
    ) -> list[StagedArtifact]:
        opening = b"--" + boundary + b"\r\n"
        closing = b"--" + boundary + b"--\r\n"
        first = stream.readline()
        if first == closing:
            return []
        if first != opening:
            raise CaptureBundleError("multipart response has an invalid opening boundary")
        artifacts: list[StagedArtifact] = []
        names: set[str] = set()
        while True:
            part_headers = _read_headers(stream)
            name = _attachment_name(part_headers.get("content-disposition", ""))
            if name in names:
                raise CaptureBundleError(f"duplicate artifact filename: {name}")
            names.add(name)
            media_type = part_headers.get("content-type")
            digest = part_headers.get("digest")
            part_capture_id = part_headers.get("x-capture-id")
            try:
                length = int(part_headers.get("content-length", ""))
            except ValueError as exc:
                raise CaptureBundleError("artifact Content-Length is invalid") from exc
            if length < 0 or not media_type or part_capture_id != capture_id:
                raise CaptureBundleError("artifact headers are invalid")
            expected_digest = _digest_value(digest)
            path = directory / name
            actual_digest = hashlib.sha256()
            remaining = length
            with path.open("xb") as output:
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise CaptureBundleError("artifact body ended before Content-Length")
                    output.write(chunk)
                    actual_digest.update(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if stream.read(2) != b"\r\n":
                raise CaptureBundleError("artifact body is not followed by CRLF")
            if actual_digest.digest() != expected_digest:
                raise CaptureBundleError(f"artifact digest mismatch: {name}")
            artifacts.append(
                StagedArtifact(
                    name=name,
                    media_type=media_type,
                    path=path,
                    size=length,
                    sha256=actual_digest.hexdigest(),
                )
            )
            marker = stream.readline()
            if marker == closing:
                if stream.read(1):
                    raise CaptureBundleError("multipart response contains trailing data")
                return artifacts
            if marker != opening:
                raise CaptureBundleError("multipart response has an invalid boundary")

    def _staging_directory(self) -> str:
        self.staging_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        return str(self.staging_root)


def _multipart_boundary(content_type: str) -> bytes:
    message = Message()
    message["content-type"] = content_type
    if message.get_content_type() != "multipart/mixed":
        raise CaptureBundleError("CaptureNode response is not multipart/mixed")
    boundary = message.get_param("boundary")
    if not isinstance(boundary, str) or not boundary or not boundary.isascii():
        raise CaptureBundleError("multipart response has no valid boundary")
    return boundary.encode("ascii")


def _read_headers(stream: BinaryIO) -> dict[str, str]:
    result: dict[str, str] = {}
    while True:
        line = stream.readline(8193)
        if not line:
            raise CaptureBundleError("multipart response ended in part headers")
        if len(line) > 8192:
            raise CaptureBundleError("multipart part header is too long")
        if line == b"\r\n":
            return result
        try:
            name, value = line.decode("ascii").rstrip("\r\n").split(":", 1)
        except (UnicodeDecodeError, ValueError) as exc:
            raise CaptureBundleError("multipart part header is invalid") from exc
        lowered = name.strip().lower()
        if lowered in result:
            raise CaptureBundleError(f"duplicate multipart part header: {lowered}")
        result[lowered] = value.strip()


def _attachment_name(value: str) -> str:
    message = Message()
    message["content-disposition"] = value
    if message.get_content_disposition() != "attachment":
        raise CaptureBundleError("artifact is not an attachment")
    name = message.get_filename()
    if not name or Path(name).name != name or name in {".", ".."} or "\x00" in name:
        raise CaptureBundleError("artifact filename is unsafe")
    return name


def _digest_value(value: str | None) -> bytes:
    prefix = "sha-256="
    if not value or not value.startswith(prefix):
        raise CaptureBundleError("artifact Digest header is invalid")
    try:
        digest = base64.b64decode(value[len(prefix) :], validate=True)
    except ValueError as exc:
        raise CaptureBundleError("artifact Digest header is invalid") from exc
    if len(digest) != hashlib.sha256().digest_size:
        raise CaptureBundleError("artifact Digest header is invalid")
    return digest
