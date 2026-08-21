"""Restricted managed-node adapters using documented local HTTP contracts."""

from __future__ import annotations

import json
import mimetypes
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .errors import SupervisorError
from .models import ActivityPlan, DataRecord, NodeDefinition

_MEDIA_TYPES = {".dng": "image/x-adobe-dng"}


class AdapterError(SupervisorError):
    """A managed node rejected a request or returned an invalid response."""


class PreviewByteStream:
    """Closable byte iterator that owns one upstream MJPEG response."""

    def __init__(self, source: BinaryIO, media_type: str) -> None:
        self.source = source
        self.media_type = media_type
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        try:
            while True:
                chunk = self.source.read(65536)
                if not chunk:
                    return
                yield chunk
        finally:
            self.close()

    def read_frame(self) -> tuple[bytes, str] | None:
        """Read one complete upstream multipart frame, or None at clean EOF."""

        return _read_multipart_frame_or_eof(self.source)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.source.close()


class JsonTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        timeout: float,
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class UrllibJsonTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        timeout: float,
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        body = None
        if document is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise AdapterError(
                f"managed node returned HTTP {exc.code} for {method} {url}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AdapterError(f"managed node request failed for {method} {url}: {exc}") from exc
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"managed node returned invalid JSON for {method} {url}") from exc
        if not isinstance(value, dict):
            raise AdapterError(f"managed node returned a non-object for {method} {url}")
        return value


class CaptureNodeClient:
    """Client for CaptureNode's existing Web API; never executes shell commands."""

    def __init__(
        self,
        endpoint: str,
        *,
        request_timeout_seconds: float = 10,
        completion_timeout_seconds: float = 60,
        transport: JsonTransport | None = None,
        preview_opener=urlopen,
        sleeper=time.sleep,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds
        self.completion_timeout_seconds = completion_timeout_seconds
        self.transport = transport or UrllibJsonTransport()
        self.preview_opener = preview_opener
        self.sleeper = sleeper

    def _request(
        self,
        method: str,
        path: str,
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.transport.request(
            method,
            f"{self.endpoint}{path}",
            timeout=self.request_timeout_seconds,
            document=document,
        )

    def health(self) -> dict[str, Any]:
        value = self._request("GET", "/api/health")
        if value.get("status") != "running":
            raise AdapterError("CaptureNode is not running")
        if value.get("camera_connected") is not True:
            raise AdapterError(
                f"CaptureNode camera is unavailable: {value.get('camera_detail') or 'unknown reason'}"
            )
        return value

    def capture(self) -> dict[str, Any]:
        value = self._request("POST", "/api/capture")
        if not isinstance(value.get("capture_id"), str) or not value["capture_id"]:
            raise AdapterError("CaptureNode capture response has no capture_id")
        if not isinstance(value.get("captured_at"), str):
            raise AdapterError("CaptureNode capture response has no captured_at")
        return value

    def preview_frame(self) -> tuple[bytes, str]:
        request = Request(
            f"{self.endpoint}/api/preview",
            method="GET",
            headers={"Accept": "multipart/x-mixed-replace"},
        )
        try:
            with self.preview_opener(
                request, timeout=self.request_timeout_seconds
            ) as response:
                return _read_multipart_frame(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise AdapterError(
                f"CaptureNode preview returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AdapterError(f"CaptureNode preview request failed: {exc}") from exc

    def open_preview_stream(self) -> PreviewByteStream:
        request = Request(
            f"{self.endpoint}/api/preview",
            method="GET",
            headers={"Accept": "multipart/x-mixed-replace"},
        )
        try:
            response = self.preview_opener(
                request, timeout=self.request_timeout_seconds
            )
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise AdapterError(
                f"CaptureNode preview returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AdapterError(f"CaptureNode preview request failed: {exc}") from exc
        media_type = response.headers.get("Content-Type", "")
        if not media_type.lower().startswith("multipart/x-mixed-replace;"):
            response.close()
            raise AdapterError("CaptureNode preview is not an MJPEG stream")
        return PreviewByteStream(response, media_type)

    def configure_capture(self, parameters: dict[str, Any]) -> dict[str, Any]:
        document = self._request("GET", "/api/config")
        capture = document.get("capture")
        if not isinstance(capture, dict):
            raise AdapterError("CaptureNode configuration has no capture object")
        candidate = {**document, "capture": {**capture, "params": parameters}}
        return self._request("PUT", "/api/config", candidate)

    def events(self, *, page: int = 1, page_size: int = 50) -> list[dict[str, Any]]:
        query = urlencode({"page": page, "page_size": page_size})
        value = self._request("GET", f"/api/events?{query}")
        items = value.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise AdapterError("CaptureNode events response has invalid items")
        return items

    def wait_for_terminal(self, capture_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.completion_timeout_seconds
        while True:
            for event in self.events():
                if event.get("capture_id") != capture_id:
                    continue
                if event.get("event_type") == "terminal.completed":
                    return event
                if event.get("event_type") in {"terminal.discarded", "capture.failed"}:
                    raise AdapterError(
                        f"CaptureNode capture {capture_id} failed: {event.get('failures') or event.get('cause')}"
                    )
            if time.monotonic() >= deadline:
                raise AdapterError(
                    f"CaptureNode capture {capture_id} did not reach a terminal state"
                )
            self.sleeper(min(0.25, max(0, deadline - time.monotonic())))

    def storage_targets(self) -> list[str]:
        value = self._request("GET", "/api/storage/targets")
        items = value.get("items")
        if not isinstance(items, list):
            raise AdapterError("CaptureNode storage targets response is invalid")
        targets: list[str] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise AdapterError("CaptureNode storage target is invalid")
            targets.append(item["name"])
        return targets

    def storage_files(
        self, target: str, *, page: int = 1, page_size: int = 50
    ) -> list[dict[str, Any]]:
        items, _pages = self._storage_page(target, page=page, page_size=page_size)
        return items

    def _storage_page(
        self, target: str, *, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int]:
        query = urlencode({"target": target, "page": page, "page_size": page_size})
        value = self._request("GET", f"/api/storage/files?{query}")
        items = value.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise AdapterError("CaptureNode storage listing is invalid")
        pages = value.get("pages", 1)
        if type(pages) is not int or pages < 1:
            raise AdapterError("CaptureNode storage listing has invalid pages")
        return items, pages

    def data_records(
        self,
        *,
        node_id: str,
        activity_id: str | None = None,
        run_id: str | None = None,
        capture_id: str | None = None,
    ) -> list[DataRecord]:
        records: list[DataRecord] = []
        indexed_at = datetime.now(UTC)
        for target in self.storage_targets():
            page = 1
            while True:
                items, pages = self._storage_page(target, page=page, page_size=50)
                for item in items:
                    name = item.get("name")
                    size = item.get("size")
                    modified_at = item.get("modified_at")
                    if not isinstance(name, str) or not isinstance(modified_at, str):
                        continue
                    if capture_id and capture_id not in name:
                        continue
                    try:
                        captured_at = datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    encoded_target = quote(target, safe="")
                    encoded_name = quote(name, safe="")
                    records.append(
                        DataRecord(
                            data_id=f"{node_id}:{target}:{name}",
                            node_id=node_id,
                            activity_id=activity_id,
                            run_id=run_id,
                            kind="image",
                            captured_at=captured_at,
                            uri=f"{self.endpoint}/api/storage/files/{encoded_target}/{encoded_name}",
                            media_type=(
                                _MEDIA_TYPES.get(Path(name).suffix.lower())
                                or mimetypes.guess_type(name)[0]
                            ),
                            size=size if type(size) is int else None,
                            integrity="available",
                            summary=f"CaptureNode {target}/{name}",
                            indexed_at=indexed_at,
                        )
                    )
                if page >= pages:
                    break
                page += 1
        return records


class ManagedNodeAdapter(Protocol):
    node_id: str

    def health(self) -> dict[str, Any]: ...
    def execute(self, activity: ActivityPlan, run_id: str) -> dict[str, Any]: ...
    def preview_frame(self) -> tuple[bytes, str]: ...
    def open_preview_stream(self) -> PreviewByteStream: ...
    def list_data(
        self,
        *,
        activity_id: str | None = None,
        run_id: str | None = None,
        capture_id: str | None = None,
    ) -> list[DataRecord]: ...


class CaptureNodeAdapter:
    def __init__(self, definition: NodeDefinition, client: CaptureNodeClient) -> None:
        self.definition = definition
        self.client = client
        self.node_id = definition.id

    def health(self) -> dict[str, Any]:
        return self.client.health()

    def execute(self, activity: ActivityPlan, run_id: str) -> dict[str, Any]:
        self.health()
        self.client.configure_capture(activity.parameters)
        capture = self.client.capture()
        terminal = self.client.wait_for_terminal(capture["capture_id"])
        return {
            "capture_id": capture["capture_id"],
            "captured_at": capture["captured_at"],
            "terminal_event": terminal,
            "run_id": run_id,
        }

    def capture_events(self) -> list[dict[str, Any]]:
        """Return recent CaptureNode events for native-timer observation."""

        return self.client.events(page_size=50)

    def preview_frame(self) -> tuple[bytes, str]:
        return self.client.preview_frame()

    def open_preview_stream(self) -> PreviewByteStream:
        return self.client.open_preview_stream()

    def list_data(
        self,
        *,
        activity_id: str | None = None,
        run_id: str | None = None,
        capture_id: str | None = None,
    ) -> list[DataRecord]:
        return self.client.data_records(
            node_id=self.node_id,
            activity_id=activity_id,
            run_id=run_id,
            capture_id=capture_id,
        )


def build_adapters(
    definitions,
    *,
    runtime=None,
    timezone: str = "Asia/Seoul",
) -> dict[str, ManagedNodeAdapter]:
    adapters: dict[str, ManagedNodeAdapter] = {}
    for definition in definitions:
        if definition.sensor_serial is not None:
            from .sensor_adapter import CwtSensorAdapter, HuintechCwtProvider
            from .sensor_storage import SensorStorageCoordinator

            adapters[definition.id] = CwtSensorAdapter(
                definition,
                HuintechCwtProvider(definition),
                (
                    SensorStorageCoordinator(
                        node_id=definition.id,
                        timezone=timezone,
                        settings=definition.sensor_storage,
                        runtime=runtime,
                    )
                    if definition.sensor_storage is not None and runtime is not None
                    else None
                ),
            )
            continue
        if definition.kind != "capture-node" or definition.endpoint is None:
            continue
        client = CaptureNodeClient(
            definition.endpoint,
            request_timeout_seconds=definition.request_timeout_seconds,
            completion_timeout_seconds=definition.completion_timeout_seconds,
        )
        adapters[definition.id] = CaptureNodeAdapter(definition, client)
    return adapters


def _read_multipart_frame(stream: BinaryIO) -> tuple[bytes, str]:
    frame = _read_multipart_frame_or_eof(stream)
    if frame is None:
        raise AdapterError("CaptureNode preview has no multipart boundary")
    return frame


def _read_multipart_frame_or_eof(
    stream: BinaryIO,
) -> tuple[bytes, str] | None:
    while True:
        boundary = stream.readline(256)
        if not boundary:
            return None
        if boundary not in {b"\r\n", b"\n"}:
            break
    if not boundary.startswith(b"--"):
        raise AdapterError("CaptureNode preview has no multipart boundary")
    headers: dict[str, str] = {}
    header_bytes = 0
    while True:
        line = stream.readline(8192)
        header_bytes += len(line)
        if not line or header_bytes > 32768:
            raise AdapterError("CaptureNode preview has invalid frame headers")
        if line in {b"\r\n", b"\n"}:
            break
        try:
            name, value = line.decode("ascii").split(":", 1)
        except (UnicodeDecodeError, ValueError) as exc:
            raise AdapterError("CaptureNode preview has malformed frame headers") from exc
        headers[name.strip().lower()] = value.strip()
    media_type = headers.get("content-type", "")
    if media_type not in {"image/jpeg", "image/png"}:
        raise AdapterError("CaptureNode preview frame has an unsupported media type")
    try:
        length = int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise AdapterError("CaptureNode preview frame has no valid content length") from exc
    if length < 1 or length > 20 * 1024 * 1024:
        raise AdapterError("CaptureNode preview frame size is outside the allowed range")
    data = stream.read(length)
    if len(data) != length:
        raise AdapterError("CaptureNode preview frame ended unexpectedly")
    return data, media_type
