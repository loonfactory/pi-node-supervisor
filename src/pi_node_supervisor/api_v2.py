"""Authenticated HTTP API for the v2 Supervisor runtime."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .application_v2 import RuntimeControllerV2
from .errors import ApplyError, ManualOperationUnavailable, SupervisorError
from .service_v2 import SupervisorServiceV2


class ManualCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str | None = Field(None, alias="deviceId")
    parameters: dict[str, Any] = Field(default_factory=dict)


def create_app_v2(
    service: SupervisorServiceV2,
    controller: RuntimeControllerV2,
    *,
    api_token: str,
    manage_runtime: bool = True,
    static_directory: Path | None = None,
) -> FastAPI:
    if not api_token:
        raise ValueError("api_token must not be empty")
    worker_task: asyncio.Task[None] | None = None
    stop_worker = asyncio.Event()

    async def worker() -> None:
        while not stop_worker.is_set():
            try:
                await controller.tick()
            except Exception as exc:
                controller.runtime.append_event(
                    "runtime.tick_failed",
                    severity="error",
                    payload={"error": f"{type(exc).__name__}: {exc}"},
                )
            try:
                await asyncio.wait_for(stop_worker.wait(), timeout=1)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal worker_task
        if manage_runtime:
            await asyncio.to_thread(controller.start)
            worker_task = asyncio.create_task(worker())
        try:
            yield
        finally:
            stop_worker.set()
            if worker_task is not None:
                await worker_task
            if manage_runtime:
                await asyncio.to_thread(controller.stop)

    app = FastAPI(title="Pi Node Supervisor", version="2", lifespan=lifespan)

    async def authenticated(authorization: str | None = Header(None)) -> None:
        expected = f"Bearer {api_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="authentication required")

    preview_cookie = "supervisor-preview"
    preview_session_seconds = 8 * 60 * 60

    def preview_signature(expiry: str) -> str:
        return hmac.new(
            api_token.encode(), f"preview:{expiry}".encode(), "sha256"
        ).hexdigest()

    async def preview_authenticated(request: Request) -> None:
        value = request.cookies.get(preview_cookie, "")
        expiry, _, signature = value.partition(".")
        if (
            not expiry.isascii()
            or not expiry.isdigit()
            or len(expiry) > 12
            or not signature.isascii()
            or not hmac.compare_digest(signature, preview_signature(expiry))
            or int(expiry) <= time.time()
        ):
            raise HTTPException(status_code=401, detail="preview authentication required")

    @app.post("/api/camera-session", dependencies=[Depends(authenticated)])
    async def camera_session(request: Request) -> Response:
        expiry = str(int(time.time()) + preview_session_seconds)
        response = Response(status_code=204)
        response.set_cookie(
            preview_cookie,
            f"{expiry}.{preview_signature(expiry)}",
            max_age=preview_session_seconds,
            path="/api/camera/",
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(SupervisorError)
    async def supervisor_error(_request, exc: SupervisorError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ManualOperationUnavailable)
    async def manual_operation_unavailable(_request, exc: ManualOperationUnavailable):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "running",
            "schemaVersion": 2,
            "active": service.active_resolution() is not None,
            "activeWork": controller.active_work.count,
        }

    @app.get("/api/dashboard", dependencies=[Depends(authenticated)])
    async def dashboard() -> dict[str, Any]:
        return {
            **controller.runtime.counts(),
            "activeWork": controller.active_work.count,
            "nodes": controller.node_documents(),
        }

    @app.post(
        "/api/dashboard/acknowledge-failures",
        dependencies=[Depends(authenticated)],
    )
    async def acknowledge_dashboard_failures() -> dict[str, int]:
        return {"acknowledged": controller.runtime.acknowledge_failed_runs()}

    @app.get("/api/config/draft", dependencies=[Depends(authenticated)])
    async def get_draft() -> dict[str, Any]:
        return service.draft() or {}

    @app.put("/api/config/draft", dependencies=[Depends(authenticated)])
    async def put_draft(document: dict[str, Any]) -> dict[str, Any]:
        return service.save_draft(document)

    @app.get("/api/config/active", dependencies=[Depends(authenticated)])
    async def get_active() -> dict[str, Any]:
        return service.active() or {}

    @app.post("/api/plan/preview", dependencies=[Depends(authenticated)])
    async def preview(document: dict[str, Any]) -> dict[str, Any]:
        return service.preview(document)

    @app.post("/api/revisions/apply", dependencies=[Depends(authenticated)])
    async def apply_revision(
        document: dict[str, Any] | None = Body(None),
    ) -> dict[str, Any]:
        previous = service.active_resolution()
        result = (
            service.apply_document(document)
            if document is not None
            else service.apply_draft()
        )
        if manage_runtime:
            try:
                await asyncio.to_thread(controller.reload)
            except Exception as exc:
                detail = f"runtime reload failed: {type(exc).__name__}: {exc}"
                await asyncio.to_thread(
                    service.store.rollback_activation,
                    previous[0] if previous else None,
                    result["id"],
                    active_configuration_path=service.active_configuration_path,
                    generated_capture_directory=service.generated_capture_directory,
                    power_configuration_path=service.power_configuration_path,
                    error=detail,
                )
                await asyncio.to_thread(controller.reload)
                raise ApplyError(detail) from exc
        return result

    @app.get("/api/revisions", dependencies=[Depends(authenticated)])
    async def revisions() -> dict[str, Any]:
        return {"items": service.store.list_revisions()}

    @app.get("/api/power-manager/config", dependencies=[Depends(authenticated)])
    async def power_config() -> dict[str, Any]:
        try:
            return json.loads(service.power_configuration_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    @app.post("/api/runtime/tick", dependencies=[Depends(authenticated)])
    async def runtime_tick() -> dict[str, Any]:
        return {"items": [_document(item) for item in await controller.tick()]}

    @app.post("/api/runtime/recover", dependencies=[Depends(authenticated)])
    async def runtime_recover() -> dict[str, Any]:
        return {
            "items": [
                _document(item) for item in controller.runtime.recover_interrupted()
            ]
        }

    @app.get("/api/runs", dependencies=[Depends(authenticated)])
    async def runs(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        counts = controller.runtime.counts()
        records = controller.runtime.list_runs(limit, offset)
        return {
            "items": [
                {
                    **_document(item),
                    "storageResults": [
                        _document(result)
                        for result in controller.runtime.list_storage_results(item.run_id)
                    ],
                }
                for item in records
            ],
            "total": counts["runs"],
        }

    @app.get("/api/events", dependencies=[Depends(authenticated)])
    async def events(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        counts = controller.runtime.counts()
        return {
            "items": [_document(item) for item in controller.runtime.list_events(limit, offset)],
            "total": counts["events"],
        }

    @app.get("/api/data", dependencies=[Depends(authenticated)])
    async def data(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        counts = controller.runtime.counts()
        return {
            "items": [_document(item) for item in controller.runtime.list_data(limit, offset)],
            "total": counts["artifacts"],
        }

    @app.get("/api/nodes", dependencies=[Depends(authenticated)])
    async def nodes() -> dict[str, Any]:
        return {"items": controller.node_documents()}

    @app.get(
        "/api/nodes/{device_id}/configuration",
        dependencies=[Depends(authenticated)],
    )
    async def node_configuration(device_id: str) -> dict[str, Any]:
        active = service.active_resolution()
        if active is None:
            raise HTTPException(status_code=404, detail="no active revision")
        _revision_id, resolution = active
        try:
            return resolution.capture_node_configs[device_id]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="camera does not exist") from exc

    def camera_endpoint(device_id: str) -> str:
        if controller.process_manager is None:
            raise HTTPException(status_code=503, detail="CaptureNode is not running")
        endpoint = controller.process_manager.endpoints.get(device_id)
        if endpoint is None:
            raise HTTPException(status_code=404, detail="camera does not exist")
        return endpoint

    async def preview_response(device_id: str) -> StreamingResponse:
        endpoint = camera_endpoint(device_id)
        try:
            reader, writer, media_type, chunked = await _open_preview_stream(endpoint)
        except (TimeoutError, OSError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"preview stream failed: {exc}") from exc

        async def chunks():
            try:
                if chunked:
                    async for chunk in _chunked_body(reader):
                        yield chunk
                else:
                    while chunk := await reader.read(64 * 1024):
                        yield chunk
            finally:
                writer.close()
                await writer.wait_closed()

        return StreamingResponse(
            chunks(),
            media_type=media_type,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/camera/{device_id}", dependencies=[Depends(preview_authenticated)])
    async def camera_preview(device_id: str) -> StreamingResponse:
        return await preview_response(device_id)

    @app.get(
        "/api/nodes/{device_id}/preview",
        dependencies=[Depends(authenticated)],
    )
    async def node_preview(device_id: str) -> StreamingResponse:
        return await preview_response(device_id)

    @app.get(
        "/api/nodes/{device_id}/preview/stream",
        dependencies=[Depends(authenticated)],
    )
    async def node_preview_stream(device_id: str) -> StreamingResponse:
        return await preview_response(device_id)

    @app.post("/api/captures/manual", dependencies=[Depends(authenticated)])
    async def manual_capture(
        request: ManualCaptureRequest | None = Body(None),
    ) -> dict[str, Any]:
        request = request or ManualCaptureRequest()
        if request.device_id:
            result = await controller.capture_one(
                request.device_id, request.parameters
            )
            return {"items": [_document(result)]}
        return {"items": [_document(item) for item in await controller.capture_all()]}

    @app.post("/api/sensors/collect", dependencies=[Depends(authenticated)])
    async def collect_sensors() -> dict[str, Any]:
        return {
            "items": [
                _document(item) for item in await controller.collect_all_sensors()
            ]
        }

    web_dist = static_directory or Path(__file__).with_name("web") / "static"
    if web_dist.is_dir():
        assets = web_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str):
            requested = (web_dist / path).resolve()
            if requested.is_file() and requested.is_relative_to(web_dist.resolve()):
                return FileResponse(requested)
            return FileResponse(web_dist / "index.html")

    return app


def _document(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {_camel(name): _document(item) for name, item in value.items()}
    if isinstance(value, list):
        return [_document(item) for item in value]
    return value


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(item[:1].upper() + item[1:] for item in tail)


async def _open_preview_stream(
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
        media_type, chunked = _parse_preview_headers(header)
        return reader, writer, media_type, chunked
    except BaseException:
        writer.close()
        await writer.wait_closed()
        raise


def _parse_preview_headers(header: bytes) -> tuple[str, bool]:
    lines = header[:-4].split(b"\r\n")
    if not lines or not lines[0].startswith(b"HTTP/1.1 200 "):
        status = lines[0].decode("ascii", errors="replace") if lines else "empty response"
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


async def _chunked_body(reader: asyncio.StreamReader):
    while True:
        line = await reader.readline()
        if not line:
            raise OSError("CaptureNode preview ended before a chunk header")
        try:
            size = int(line.split(b";", 1)[0].strip(), 16)
        except ValueError as exc:
            raise OSError("CaptureNode preview has an invalid chunk header") from exc
        if size < 0 or size > 16 * 1024 * 1024:
            raise OSError("CaptureNode preview chunk is out of range")
        if size == 0:
            while trailer := await reader.readline():
                if trailer == b"\r\n":
                    return
            return
        remaining = size
        while remaining:
            data = await reader.read(min(64 * 1024, remaining))
            if not data:
                raise OSError("CaptureNode preview ended inside a chunk")
            remaining -= len(data)
            yield data
        if await reader.readexactly(2) != b"\r\n":
            raise OSError("CaptureNode preview chunk is missing its trailing CRLF")
