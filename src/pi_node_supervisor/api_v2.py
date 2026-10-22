"""Authenticated HTTP API for the v2 Supervisor runtime."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .api_auth import ApiAuthentication
from .preview_api import create_preview_router
from .application_v2 import RuntimeControllerV2
from .errors import ManualOperationUnavailable, SupervisorError
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
                await controller.shutdown()

    app = FastAPI(title="Pi Node Supervisor", version="2", lifespan=lifespan)

    authentication = ApiAuthentication(api_token)
    authenticated = authentication.bearer
    app.include_router(create_preview_router(controller, authentication))

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
        return await controller.apply_revision(document, restart=manage_runtime)

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
