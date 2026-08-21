"""Authenticated local FastAPI boundary for the planner and revision service."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import urlopen

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from .adapters import AdapterError
from .adapters import PreviewByteStream
from .errors import ApplyError, PersistenceError, PlanningError
from .executor import ExecutionEngine, ExecutionWorker
from .models import (
    CaptureNodeSettings,
    CaptureScheduleSettings,
    CaptureScheduleUpdate,
    DataRecord,
    PowerSettingsUpdate,
    SensorScheduleUpdate,
    StorageSettingsDocument,
)
from .process_manager import CaptureProcessError
from .service import SupervisorService


_MAX_CATALOG_FILE_BYTES = 128 * 1024 * 1024


def _catalog_storage_location(record: DataRecord) -> str:
    path = urlsplit(record.uri).path
    prefix = "/api/storage/files/"
    if path.startswith(prefix):
        return unquote(path[len(prefix):])
    return record.summary.removeprefix("CaptureNode ")


def _catalog_uri_allowed(record: DataRecord, endpoint: str | None) -> bool:
    if endpoint is None:
        return False
    expected = urlsplit(endpoint)
    actual = urlsplit(record.uri)
    return (
        actual.scheme in {"http", "https"}
        and (actual.scheme, actual.netloc) == (expected.scheme, expected.netloc)
        and actual.path.startswith("/api/storage/files/")
        and not actual.username
        and not actual.password
        and not actual.query
        and not actual.fragment
    )


class ProcessManager(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def statuses(self) -> list[dict[str, Any]]: ...
    def camera_configuration(self, node_id: str) -> dict[str, Any]: ...
    def update_storages(
        self, node_id: str, settings: StorageSettingsDocument
    ) -> dict[str, Any]: ...
    def update_all_storages(
        self, settings: StorageSettingsDocument
    ) -> dict[str, Any]: ...
    def apply_native_schedules(
        self,
        settings_by_node: dict[str, Any],
        activate: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]: ...
    def apply_settings_overlays(
        self,
        settings: CaptureNodeSettings,
        activate: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]: ...


async def _async_preview_chunks(stream: PreviewByteStream):
    """Proxy complete MJPEG frames while retaining only the latest pending one."""

    stop = threading.Event()
    condition = threading.Condition()
    latest: bytes | None = None
    version = 0
    finished = False
    failure: BaseException | None = None

    def produce() -> None:
        nonlocal latest, version, finished, failure
        try:
            while not stop.is_set():
                frame = stream.read_frame()
                if frame is None:
                    break
                data, media_type = frame
                part = (
                    b"--frame\r\n"
                    + f"Content-Type: {media_type}\r\n".encode("ascii")
                    + f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
                    + data
                    + b"\r\n"
                )
                with condition:
                    latest = part
                    version += 1
                    condition.notify_all()
        except BaseException as exc:
            failure = exc
        finally:
            with condition:
                finished = True
                condition.notify_all()

    producer = threading.Thread(
        target=produce,
        name="pi-node-supervisor-preview-proxy",
        daemon=True,
    )
    producer.start()
    seen_version = 0
    try:
        while True:
            with condition:
                if version != seen_version:
                    item = latest
                    seen_version = version
                    terminal = False
                else:
                    item = None
                    terminal = finished
                    error = failure
            if item is not None:
                yield item
                continue
            if terminal:
                if error is not None:
                    raise error
                return
            else:
                await asyncio.sleep(0.005)
    finally:
        stop.set()
        stream.close()
        producer.join(timeout=1)


def create_app(
    service: SupervisorService,
    *,
    api_token: str,
    engine: ExecutionEngine | None = None,
    manage_worker: bool = False,
    process_manager: ProcessManager | None = None,
    static_directory: Path | None = None,
) -> FastAPI:
    if not api_token:
        raise ValueError("api_token must not be empty")
    worker = ExecutionWorker(engine) if engine is not None and manage_worker else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if process_manager is not None:
            process_manager.start()
        try:
            if worker is not None:
                worker.start()
            elif engine is not None:
                engine.recover()
            try:
                yield
            finally:
                if worker is not None:
                    worker.stop()
        finally:
            try:
                if engine is not None:
                    engine.close()
            finally:
                if process_manager is not None:
                    process_manager.stop()

    app = FastAPI(
        title="pi-node-supervisor", version="0.1.0", lifespan=lifespan
    )

    async def authenticated(x_supervisor_token: str | None = Header(default=None)) -> None:
        if x_supervisor_token is None or not hmac.compare_digest(
            x_supervisor_token, api_token
        ):
            raise HTTPException(status_code=401, detail="invalid supervisor token")

    @app.exception_handler(ValidationError)
    async def validation_error(_request, exc: ValidationError):
        return JSONResponse(
            status_code=422,
            content={"code": "validation_failed", "detail": exc.errors()},
        )

    @app.exception_handler(PlanningError)
    async def planning_error(_request, exc: PlanningError):
        return JSONResponse(
            status_code=422,
            content={"code": "planning_failed", "detail": str(exc)},
        )

    @app.exception_handler(ApplyError)
    async def apply_error(_request, exc: ApplyError):
        return JSONResponse(
            status_code=409,
            content={"code": "apply_failed", "detail": str(exc)},
        )

    @app.exception_handler(PersistenceError)
    async def persistence_error(_request, exc: PersistenceError):
        return JSONResponse(
            status_code=500,
            content={"code": "persistence_failed", "detail": str(exc)},
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        result = {"status": "running", **service.store.status()}
        if engine is not None:
            result["runtime"] = engine.runtime.counts()
        return result

    @app.get("/api/dashboard", dependencies=[Depends(authenticated)])
    async def dashboard() -> dict[str, Any]:
        bundle = service.store.active_bundle()
        node_status = (
            engine.node_health() if engine is not None else []
        )
        return {
            "revision": service.store.status(),
            "runtime": engine.runtime.counts() if engine is not None else None,
            "active_plan": (
                bundle[2].model_dump(mode="json") if bundle is not None else None
            ),
            "nodes": node_status,
            "managed_processes": (
                process_manager.statuses() if process_manager is not None else []
            ),
        }

    @app.get("/api/config/draft", dependencies=[Depends(authenticated)])
    async def draft() -> dict[str, Any]:
        value = service.draft()
        if value is None:
            raise HTTPException(status_code=404, detail="draft does not exist")
        return value

    @app.get("/api/config/active", dependencies=[Depends(authenticated)])
    async def active() -> dict[str, Any]:
        value = service.active()
        if value is None:
            raise HTTPException(status_code=404, detail="active configuration does not exist")
        return value

    @app.put("/api/config/draft", dependencies=[Depends(authenticated)])
    async def put_draft(document: dict[str, Any]) -> dict[str, Any]:
        plan = service.save_draft(document)
        return {"saved": True, "plan": plan.model_dump(mode="json")}

    @app.post("/api/plan/preview", dependencies=[Depends(authenticated)])
    async def preview(document: dict[str, Any]) -> dict[str, Any]:
        return service.preview_with_power(document)

    @app.post("/api/revisions/apply", dependencies=[Depends(authenticated)])
    async def apply_draft() -> dict[str, Any]:
        draft_document = service.draft()
        if draft_document is None:
            raise HTTPException(status_code=404, detail="draft does not exist")
        settings = CaptureNodeSettings.model_validate(
            draft_document.get("capture_node_settings", {})
        )
        applied = None
        if settings.common or settings.nodes:
            if process_manager is None:
                raise HTTPException(
                    status_code=503,
                    detail="managed CaptureNode configuration is disabled",
                )
            try:
                applied = process_manager.apply_settings_overlays(
                    settings, service.apply_draft
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except CaptureProcessError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            revision = applied["revision"]
        else:
            revision = service.apply_draft()
        if engine is not None:
            engine.runtime.append_event(
                "configuration.revision_applied",
                payload={
                    "revision_id": revision["id"],
                    "source": "advanced-json",
                    "capture_node_updates": (
                        applied["updated_nodes"] if applied is not None else []
                    ),
                },
            )
            active_document = service.active() or {}
            empty_nodes = sorted(
                item["node"]
                for item in active_document.get("capture_schedules", [])
                if item.get("mode") == "daily" and not item.get("daily_times")
            )
            if empty_nodes:
                engine.runtime.append_event(
                    "power.schedule_disabled",
                    severity="warning",
                    payload={
                        "node_ids": empty_nodes,
                        "reason": "daily capture schedule has no capture times",
                        "revision_id": revision["id"],
                    },
                )
        return revision

    @app.get(
        "/api/settings/capture-nodes",
        dependencies=[Depends(authenticated)],
    )
    async def capture_node_settings_export() -> dict[str, Any]:
        if process_manager is None:
            raise HTTPException(status_code=503, detail="managed process configuration is disabled")
        return {
            "common": {},
            "nodes": {
                node.id: {
                    key: value
                    for key, value in process_manager.camera_configuration(node.id).items()
                    if key != "node_id"
                }
                for node in service.registry
                if node.managed_capture is not None
            },
        }

    @app.get("/api/revisions", dependencies=[Depends(authenticated)])
    async def revisions() -> dict[str, Any]:
        return {"items": service.store.list_revisions()}

    @app.get("/api/power-manager/config", dependencies=[Depends(authenticated)])
    async def power_configuration() -> dict[str, Any]:
        value = service.power_configuration()
        if value is None:
            raise HTTPException(status_code=404, detail="power configuration does not exist")
        return value.model_dump(mode="json", by_alias=True)

    @app.post("/api/runtime/tick", dependencies=[Depends(authenticated)])
    async def runtime_tick() -> dict[str, Any]:
        if engine is None:
            raise HTTPException(status_code=503, detail="execution engine is disabled")
        items = engine.tick()
        return {"items": [item.model_dump(mode="json") for item in items]}

    @app.post("/api/runtime/recover", dependencies=[Depends(authenticated)])
    async def runtime_recover() -> dict[str, Any]:
        if engine is None:
            raise HTTPException(status_code=503, detail="execution engine is disabled")
        items = engine.recover()
        return {"items": [item.model_dump(mode="json") for item in items]}

    @app.get("/api/runs", dependencies=[Depends(authenticated)])
    async def runs(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        if engine is None:
            return {"items": []}
        return {
            "items": [
                item.model_dump(mode="json")
                for item in engine.runtime.list_runs(limit=limit, offset=offset)
            ]
        }

    @app.get("/api/events", dependencies=[Depends(authenticated)])
    async def events(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        if engine is None:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        return {
            "items": [
                item.model_dump(mode="json")
                for item in engine.runtime.list_events(limit=limit, offset=offset)
            ],
            "total": engine.runtime.count_events(),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/data", dependencies=[Depends(authenticated)])
    async def data(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        if engine is None:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        # CaptureNode owns the timer and files. Refresh its catalog only when
        # the user asks to view data instead of polling every node continuously.
        engine.sync_data()
        records = engine.runtime.list_data(limit=limit, offset=offset)
        endpoints = {node.id: node.endpoint for node in service.registry}
        return {
            "items": [
                {
                    **item.model_dump(mode="json"),
                    "storage_location": _catalog_storage_location(item),
                    "original_available": _catalog_uri_allowed(
                        item, endpoints.get(item.node_id)
                    ),
                }
                for item in records
            ],
            "total": engine.runtime.count_data(),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/data/content", dependencies=[Depends(authenticated)])
    async def data_content(
        data_id: str = Query(min_length=1, max_length=2048),
        download: bool = Query(False),
    ) -> Response:
        if engine is None:
            raise HTTPException(status_code=503, detail="execution engine is disabled")
        record = engine.runtime.get_data(data_id)
        if record is None:
            raise HTTPException(status_code=404, detail="저장된 파일 정보를 찾을 수 없습니다.")
        node = next((item for item in service.registry if item.id == record.node_id), None)
        if node is None or not _catalog_uri_allowed(record, node.endpoint):
            raise HTTPException(status_code=409, detail="이 원본은 Supervisor에서 열 수 없습니다.")
        if record.size is not None and record.size > _MAX_CATALOG_FILE_BYTES:
            raise HTTPException(status_code=413, detail="원본 파일이 웹에서 열기에는 너무 큽니다.")

        def read_original() -> tuple[bytes, str]:
            with urlopen(record.uri, timeout=node.request_timeout_seconds) as upstream:
                content = upstream.read(_MAX_CATALOG_FILE_BYTES + 1)
                media_type = upstream.headers.get("Content-Type", "")
            return content, media_type

        try:
            content, upstream_media_type = read_original()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise HTTPException(
                status_code=502, detail="CaptureNode에서 원본 파일을 가져오지 못했습니다."
            ) from exc
        if len(content) > _MAX_CATALOG_FILE_BYTES:
            raise HTTPException(status_code=413, detail="원본 파일이 웹에서 열기에는 너무 큽니다.")
        filename = Path(_catalog_storage_location(record)).name or "original"
        disposition = "attachment" if download else "inline"
        return Response(
            content,
            media_type=record.media_type or upstream_media_type or "application/octet-stream",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/data/sync", dependencies=[Depends(authenticated)])
    async def sync_data(node_id: str | None = None) -> dict[str, Any]:
        if engine is None:
            raise HTTPException(status_code=503, detail="execution engine is disabled")
        try:
            return engine.sync_data(node_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/nodes", dependencies=[Depends(authenticated)])
    async def nodes() -> dict[str, Any]:
        definitions = [
            {
                **item.model_dump(mode="json"),
                "endpoint": item.endpoint,
            }
            for item in service.registry
        ]
        health = engine.node_health() if engine is not None else []
        health_by_id = {
            item["node_id"]: item
            for item in health
        }
        return {
            "items": [
                {**definition, "health": health_by_id.get(definition["id"])}
                for definition in definitions
            ]
        }

    @app.get(
        "/api/nodes/{node_id}/configuration",
        dependencies=[Depends(authenticated)],
    )
    async def node_configuration(node_id: str) -> dict[str, Any]:
        if process_manager is None:
            raise HTTPException(status_code=503, detail="managed process configuration is disabled")
        try:
            return process_manager.camera_configuration(node_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put(
        "/api/nodes/{node_id}/storages",
        dependencies=[Depends(authenticated)],
    )
    async def update_node_storages(
        node_id: str, document: StorageSettingsDocument
    ) -> dict[str, Any]:
        if process_manager is None:
            raise HTTPException(status_code=503, detail="managed process configuration is disabled")
        try:
            return process_manager.update_storages(node_id, document)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CaptureProcessError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put(
        "/api/settings/cameras/storages",
        dependencies=[Depends(authenticated)],
    )
    async def update_common_storages(
        document: StorageSettingsDocument,
    ) -> dict[str, Any]:
        if process_manager is None:
            raise HTTPException(status_code=503, detail="managed process configuration is disabled")
        try:
            return process_manager.update_all_storages(document)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CaptureProcessError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/api/capture-schedules",
        dependencies=[Depends(authenticated)],
    )
    async def capture_schedules() -> dict[str, Any]:
        configuration = service.active()
        if configuration is None:
            raise HTTPException(status_code=404, detail="active configuration does not exist")
        cameras = [node.id for node in service.registry if node.kind == "capture-node"]
        return {
            "items": configuration.get("capture_schedules", []),
            "camera_ids": cameras,
            "power_linked": True,
        }

    @app.put(
        "/api/capture-schedules",
        dependencies=[Depends(authenticated)],
    )
    async def update_capture_schedules(
        document: CaptureScheduleUpdate,
    ) -> dict[str, Any]:
        active = service.active()
        if active is None:
            raise HTTPException(status_code=404, detail="active configuration does not exist")
        cameras = [node for node in service.registry if node.kind == "capture-node"]
        if document.target == "common":
            selected = cameras
        else:
            selected = [node for node in cameras if node.id == document.target]
            if not selected:
                raise HTTPException(
                    status_code=404,
                    detail=f"capture camera does not exist: {document.target}",
                )
        selected_ids = {node.id for node in selected}
        existing = {
            item["node"]: item
            for item in active.get("capture_schedules", [])
            if isinstance(item, dict) and isinstance(item.get("node"), str)
        }
        previous_disabled_nodes = sorted(
            node_id
            for node_id in selected_ids
            if existing.get(node_id, {}).get("mode") == "daily"
            and not existing.get(node_id, {}).get("daily_times")
        )
        retained = [
            item
            for item in active.get("capture_schedules", [])
            if not isinstance(item, dict) or item.get("node") not in selected_ids
        ]
        settings = document.settings.model_dump(mode="json")
        anchor = None
        if document.settings.mode == "interval":
            assert document.settings.interval_seconds is not None
            anchor = datetime.now(UTC) + timedelta(
                seconds=document.settings.interval_seconds
            )
        replacements = []
        if document.settings.mode != "disabled":
            for node in selected:
                previous = existing.get(node.id, {})
                digest = hashlib.sha256(node.id.encode("utf-8")).hexdigest()[:8]
                replacements.append(
                    {
                        **settings,
                        "id": previous.get("id", f"auto-{node.id[:70]}-{digest}"),
                        "node": node.id,
                        "profile": previous.get("profile", node.profiles[0] if node.profiles else "default"),
                        "anchor_at": anchor.isoformat().replace("+00:00", "Z") if anchor else None,
                        "duration_seconds": previous.get("duration_seconds"),
                        "parameters": previous.get("parameters", {}),
                    }
                )
        candidate = {**active, "capture_schedules": [*retained, *replacements]}
        power_plan_disabled = (
            document.settings.mode == "daily"
            and not document.settings.daily_times
        )
        native_settings = (
            CaptureScheduleSettings(
                mode="disabled",
                interval_seconds=None,
                daily_times=(),
                timezone=document.settings.timezone,
            )
            if power_plan_disabled
            else document.settings
        )

        # Validate the complete power plan before changing CaptureNode's native
        # timer. A rejected plan must not interrupt the active shooting behavior.
        service.preview(candidate)

        if process_manager is None:
            raise HTTPException(
                status_code=503,
                detail="CaptureNode schedule coordination requires managed processes",
            )
        try:
            coordinated = process_manager.apply_native_schedules(
                {
                    node_id: native_settings
                    for node_id in sorted(selected_ids)
                },
                lambda: service.apply_document(candidate),
            )
        except (ValueError, CaptureProcessError) as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "촬영 설정을 적용하지 못했습니다. "
                    "카메라는 기존 설정으로 복구되었습니다. 이벤트에서 상태를 확인해 주세요."
                ),
            ) from exc
        if engine is not None:
            revision_id = coordinated["revision"]["id"]
            event_payload = {
                "node_ids": sorted(selected_ids),
                "mode": document.settings.mode,
                "daily_time_count": len(document.settings.daily_times),
                "interval_seconds": document.settings.interval_seconds,
                "revision_id": revision_id,
            }
            engine.runtime.append_event(
                "configuration.capture_schedule_updated",
                payload=event_payload,
            )
            if power_plan_disabled:
                engine.runtime.append_event(
                    "power.schedule_disabled",
                    severity="warning",
                    payload={
                        **event_payload,
                        "reason": "daily capture schedule has no capture times",
                    },
                )
            elif previous_disabled_nodes:
                engine.runtime.append_event(
                    "power.schedule_enabled",
                    payload={
                        **event_payload,
                        "previous_node_ids": previous_disabled_nodes,
                    },
                )
        return {
            "target": document.target,
            "items": replacements,
            "revision": coordinated["revision"],
            "native_capture": coordinated,
            "power_linked": True,
            "power_plan_disabled": power_plan_disabled,
        }

    @app.get(
        "/api/sensor-schedules",
        dependencies=[Depends(authenticated)],
    )
    async def sensor_schedules() -> dict[str, Any]:
        configuration = service.active()
        if configuration is None:
            raise HTTPException(status_code=404, detail="active configuration does not exist")
        sensor_ids = [
            node.id
            for node in service.registry
            if "sensor-collection" in node.activity_types
        ]
        return {
            "items": configuration.get("sensor_schedules", []),
            "sensor_ids": sensor_ids,
            "power_linked": True,
        }

    @app.put(
        "/api/sensor-schedules",
        dependencies=[Depends(authenticated)],
    )
    async def update_sensor_schedules(
        document: SensorScheduleUpdate,
    ) -> dict[str, Any]:
        active = service.active()
        if active is None:
            raise HTTPException(status_code=404, detail="active configuration does not exist")
        sensors = [
            node
            for node in service.registry
            if "sensor-collection" in node.activity_types
        ]
        if document.target == "common":
            selected = sensors
        else:
            selected = [node for node in sensors if node.id == document.target]
            if not selected:
                raise HTTPException(
                    status_code=404,
                    detail=f"sensor node does not exist: {document.target}",
                )
        selected_ids = {node.id for node in selected}
        existing = {
            item["node"]: item
            for item in active.get("sensor_schedules", [])
            if isinstance(item, dict) and isinstance(item.get("node"), str)
        }
        retained = [
            item
            for item in active.get("sensor_schedules", [])
            if not isinstance(item, dict) or item.get("node") not in selected_ids
        ]
        settings = document.settings.model_dump(mode="json")
        replacements = []
        if document.settings.mode != "disabled":
            for node in selected:
                previous = existing.get(node.id, {})
                digest = hashlib.sha256(node.id.encode("utf-8")).hexdigest()[:8]
                replacements.append(
                    {
                        **settings,
                        "id": previous.get(
                            "id", f"auto-sensor-{node.id[:63]}-{digest}"
                        ),
                        "node": node.id,
                        "profile": previous.get(
                            "profile", node.profiles[0] if node.profiles else "default"
                        ),
                        "duration_seconds": previous.get("duration_seconds"),
                        "parameters": previous.get("parameters", {}),
                    }
                )
        candidate = {**active, "sensor_schedules": [*retained, *replacements]}
        try:
            revision = service.apply_document(candidate)
        except (ApplyError, PersistenceError, PlanningError, ValidationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if engine is not None:
            engine.runtime.append_event(
                "configuration.sensor_schedule_updated",
                payload={
                    "node_ids": sorted(selected_ids),
                    "mode": document.settings.mode,
                    "daily_time_count": len(document.settings.daily_times),
                    "revision_id": revision["id"],
                },
            )
        return {
            "target": document.target,
            "items": replacements,
            "revision": revision,
            "power_linked": True,
        }

    @app.get(
        "/api/power-settings",
        dependencies=[Depends(authenticated)],
    )
    async def power_settings() -> dict[str, Any]:
        active = service.active()
        if active is None:
            raise HTTPException(status_code=404, detail="active configuration does not exist")
        policies = active.get("policies", {})
        return {
            "warmup_seconds": policies.get("boot-lead-seconds", 120),
            "cleanup_seconds": policies.get("shutdown-grace-seconds", 60),
            "keep_schedules": active.get("power_keep_schedules", []),
        }

    @app.put(
        "/api/power-settings",
        dependencies=[Depends(authenticated)],
    )
    async def update_power_settings(document: PowerSettingsUpdate) -> dict[str, Any]:
        active = service.active()
        if active is None:
            raise HTTPException(status_code=404, detail="active configuration does not exist")
        policies = {
            **active.get("policies", {}),
            "boot-lead-seconds": document.warmup_seconds,
            "shutdown-grace-seconds": document.cleanup_seconds,
        }
        candidate = {
            **active,
            "policies": policies,
            "power_keep_schedules": [
                item.model_dump(mode="json") for item in document.keep_schedules
            ],
        }
        service.preview(candidate)
        revision = service.apply_document(candidate)
        if engine is not None:
            engine.runtime.append_event(
                "configuration.power_settings_updated",
                payload={
                    "revision_id": revision["id"],
                    "warmup_seconds": document.warmup_seconds,
                    "cleanup_seconds": document.cleanup_seconds,
                    "power_keep_count": len(document.keep_schedules),
                },
            )
        return {
            "warmup_seconds": document.warmup_seconds,
            "cleanup_seconds": document.cleanup_seconds,
            "keep_schedules": [
                item.model_dump(mode="json") for item in document.keep_schedules
            ],
            "revision": revision,
        }

    @app.get(
        "/api/nodes/{node_id}/preview",
        dependencies=[Depends(authenticated)],
        response_class=Response,
    )
    async def node_preview(node_id: str) -> Response:
        if engine is None:
            raise HTTPException(status_code=503, detail="execution engine is disabled")
        try:
            data, media_type = engine.preview_frame(node_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AdapterError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(
            data,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(
        "/api/nodes/{node_id}/preview/stream",
        dependencies=[Depends(authenticated)],
        response_class=StreamingResponse,
    )
    async def node_preview_stream(node_id: str) -> StreamingResponse:
        if engine is None:
            raise HTTPException(status_code=503, detail="execution engine is disabled")
        try:
            stream = engine.open_preview_stream(node_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AdapterError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return StreamingResponse(
            _async_preview_chunks(stream),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    web_root = static_directory or Path(__file__).with_name("static")
    if web_root.is_dir():
        index_html = (web_root / "index.html").read_text(encoding="utf-8")
        application_js = (web_root / "app.js").read_text(encoding="utf-8")
        stylesheet = (web_root / "styles.css").read_text(encoding="utf-8")

        @app.get("/", include_in_schema=False)
        async def web_index() -> HTMLResponse:
            return HTMLResponse(index_html, headers={"Cache-Control": "no-store"})

        @app.get("/app.js", include_in_schema=False)
        async def web_script() -> Response:
            return Response(
                application_js,
                media_type="text/javascript",
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/styles.css", include_in_schema=False)
        async def web_styles() -> Response:
            return Response(
                stylesheet,
                media_type="text/css",
                headers={"Cache-Control": "no-store"},
            )

    return app
