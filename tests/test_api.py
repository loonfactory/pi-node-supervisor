from __future__ import annotations

import asyncio
import httpx
import pytest
import threading
from datetime import UTC, datetime

from pi_node_supervisor.api import (
    _async_preview_chunks,
    _catalog_storage_location,
    _catalog_uri_allowed,
    create_app,
)
from pi_node_supervisor.adapters import PreviewByteStream
from pi_node_supervisor.executor import ExecutionEngine
from pi_node_supervisor.models import DataRecord
from pi_node_supervisor.runtime_store import RuntimeStore
from pi_node_supervisor.service import SupervisorService

from test_storage_and_service import document, normalized_document


@pytest.fixture
def anyio_backend():
    return "asyncio"


def client(service: SupervisorService) -> httpx.AsyncClient:
    app = create_app(service, api_token="secret")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.anyio
async def test_preview_proxy_drops_stale_complete_frames():
    class ControlledFrames:
        media_type = "multipart/x-mixed-replace; boundary=upstream"

        def __init__(self):
            self.index = 0
            self.release_burst = threading.Event()

        def read_frame(self):
            self.index += 1
            if self.index == 1:
                return b"first", "image/jpeg"
            if self.index == 2:
                self.release_burst.wait(timeout=2)
                return b"stale", "image/jpeg"
            if self.index == 3:
                return b"latest", "image/jpeg"
            return None

        def close(self):
            self.release_burst.set()

    stream = ControlledFrames()
    chunks = _async_preview_chunks(stream)
    first = await anext(chunks)
    stream.release_burst.set()
    await asyncio.sleep(0.05)
    second = await anext(chunks)

    assert b"first" in first
    assert b"latest" in second
    assert b"stale" not in second
    with pytest.raises(StopAsyncIteration):
        await anext(chunks)


@pytest.mark.anyio
async def test_health_is_available_without_credentials(service: SupervisorService):
    async with client(service) as browser:
        response = await browser.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


@pytest.mark.anyio
async def test_web_assets_disable_cache(service: SupervisorService):
    async with client(service) as browser:
        responses = [
            await browser.get("/"),
            await browser.get("/app.js"),
            await browser.get("/styles.css"),
        ]
    assert all(response.headers["cache-control"] == "no-store" for response in responses)


@pytest.mark.anyio
async def test_configuration_endpoints_require_token(service: SupervisorService):
    async with client(service) as browser:
        assert (await browser.get("/api/config/draft")).status_code == 401
        response = await browser.get(
            "/api/config/draft", headers={"X-Supervisor-Token": "incorrect"}
        )
        assert response.status_code == 401


@pytest.mark.anyio
async def test_preview_draft_and_apply_flow(service: SupervisorService):
    headers = {"X-Supervisor-Token": "secret"}
    async with client(service) as browser:
        preview = await browser.post(
            "/api/plan/preview", json=document(), headers=headers
        )
        assert preview.status_code == 200
        assert preview.json()["pi_power_manager"]["schema_version"] == 2

        saved = await browser.put(
            "/api/config/draft", json=document(), headers=headers
        )
        assert saved.status_code == 200
        assert saved.json()["saved"] is True

        applied = await browser.post("/api/revisions/apply", headers=headers)
        assert applied.status_code == 200
        assert applied.json()["status"] == "active"
        active = await browser.get("/api/config/active", headers=headers)
        assert active.json() == normalized_document()
        revisions = await browser.get("/api/revisions", headers=headers)
        assert len(revisions.json()["items"]) == 1


@pytest.mark.anyio
async def test_planning_error_is_structured(service: SupervisorService):
    headers = {"X-Supervisor-Token": "secret"}
    invalid = document()
    invalid["activities"][0]["node"] = "missing"
    async with client(service) as browser:
        response = await browser.post(
            "/api/plan/preview", json=invalid, headers=headers
        )
    assert response.status_code == 422
    assert response.json()["code"] == "planning_failed"


@pytest.mark.anyio
async def test_runtime_catalog_endpoints_and_static_ui(service: SupervisorService):
    runtime = RuntimeStore(service.store.database_path)
    engine = ExecutionEngine(service.store, runtime, {})
    app = create_app(service, api_token="secret", engine=engine)
    headers = {"X-Supervisor-Token": "secret"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        root = await browser.get("/")
        assert root.status_code == 200
        assert root.headers["content-type"].startswith("text/html")
        app_script = await browser.get("/app.js")
        assert app_script.status_code == 200
        assert app_script.headers["content-type"].startswith("text/javascript")
        styles = await browser.get("/styles.css")
        assert styles.status_code == 200
        assert styles.headers["content-type"].startswith("text/css")

        dashboard = await browser.get("/api/dashboard", headers=headers)
        assert dashboard.status_code == 200
        assert dashboard.json()["runtime"] == {
            "runs": 0,
            "failed_runs": 0,
                "events": 0,
                "data": 0,
                "sensor_delivery_backlog": 0,
            }
        sensor_storage = await browser.get("/api/sensor-storage", headers=headers)
        assert sensor_storage.status_code == 200
        assert sensor_storage.json() == {
            "counts": {"pending": 0, "failed": 0, "succeeded": 0},
            "items": [],
        }
        assert (await browser.get("/api/runs", headers=headers)).json() == {"items": []}
        assert (await browser.get("/api/events", headers=headers)).json() == {
            "items": [],
            "total": 0,
            "limit": 100,
            "offset": 0,
        }
        assert (await browser.get("/api/data", headers=headers)).json() == {
            "items": [],
            "total": 0,
            "limit": 100,
            "offset": 0,
        }

        tick = await browser.post("/api/runtime/tick", headers=headers)
        assert tick.status_code == 200
        assert tick.json() == {"items": []}


@pytest.mark.anyio
async def test_events_endpoint_returns_pagination_metadata(service: SupervisorService):
    runtime = RuntimeStore(service.store.database_path)
    for index in range(3):
        runtime.append_event(f"test.event.{index}", node_id="camera0")
    engine = ExecutionEngine(service.store, runtime, {})
    app = create_app(service, api_token="secret", engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        response = await browser.get(
            "/api/events?limit=2&offset=1",
            headers={"X-Supervisor-Token": "secret"},
        )

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert response.json()["limit"] == 2
    assert response.json()["offset"] == 1
    assert len(response.json()["items"]) == 2


def test_catalog_original_is_limited_to_registered_capture_node_storage():
    record = DataRecord(
        data_id="camera0:local:day/image.jpg",
        node_id="camera0",
        activity_id=None,
        run_id=None,
        kind="image",
        captured_at=datetime(2030, 1, 1, tzinfo=UTC),
        uri="http://127.0.0.1:8000/api/storage/files/local/day%2Fimage.jpg",
        media_type="image/jpeg",
        size=123,
        integrity="available",
        summary="CaptureNode local/day/image.jpg",
        indexed_at=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert _catalog_storage_location(record) == "local/day/image.jpg"
    assert _catalog_uri_allowed(record, "http://127.0.0.1:8000") is True
    assert _catalog_uri_allowed(record, "http://127.0.0.1:8001") is False
    assert _catalog_uri_allowed(
        record.model_copy(update={"uri": "http://127.0.0.1:8000/api/config"}),
        "http://127.0.0.1:8000",
    ) is False


@pytest.mark.anyio
async def test_catalog_original_proxy_requires_authentication(
    service: SupervisorService, monkeypatch
):
    runtime = RuntimeStore(service.store.database_path)
    runtime.upsert_data(
        DataRecord(
            data_id="camera0:local:image.jpg",
            node_id="camera0",
            activity_id=None,
            run_id=None,
            kind="image",
            captured_at=datetime(2030, 1, 1, tzinfo=UTC),
            uri="http://127.0.0.1:8000/api/storage/files/local/image.jpg",
            media_type="image/jpeg",
            size=4,
            integrity="available",
            summary="CaptureNode local/image.jpg",
            indexed_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )

    class Original:
        headers = {"Content-Type": "image/jpeg"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b"jpeg"

    monkeypatch.setattr("pi_node_supervisor.api._catalog_uri_allowed", lambda *_: True)
    monkeypatch.setattr("pi_node_supervisor.api.urlopen", lambda *_args, **_kwargs: Original())
    engine = ExecutionEngine(service.store, runtime, {})
    app = create_app(service, api_token="secret", engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        denied = await browser.get(
            "/api/data/content?data_id=camera0%3Alocal%3Aimage.jpg"
        )
        allowed = await browser.get(
            "/api/data/content?data_id=camera0%3Alocal%3Aimage.jpg",
            headers={"X-Supervisor-Token": "secret"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.content == b"jpeg"
    assert allowed.headers["content-disposition"].startswith("inline;")


@pytest.mark.anyio
async def test_camera_preview_is_authenticated_and_not_cached(service: SupervisorService):
    class PreviewAdapter:
        node_id = "camera0"

        def health(self):
            return {"status": "running", "camera_connected": True}

        def preview_frame(self):
            return b"\xff\xd8frame\xff\xd9", "image/jpeg"

        def open_preview_stream(self):
            from io import BytesIO

            return PreviewByteStream(
                BytesIO(
                    b"--frame\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: 5\r\n\r\nframe\r\n"
                ),
                "multipart/x-mixed-replace; boundary=frame",
            )

    runtime = RuntimeStore(service.store.database_path)
    engine = ExecutionEngine(
        service.store, runtime, {"camera0": PreviewAdapter()}
    )
    app = create_app(service, api_token="secret", engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        assert (await browser.get("/api/nodes/camera0/preview")).status_code == 401
        response = await browser.get(
            "/api/nodes/camera0/preview",
            headers={"X-Supervisor-Token": "secret"},
        )
        stream_response = await browser.get(
            "/api/nodes/camera0/preview/stream",
            headers={"X-Supervisor-Token": "secret"},
        )
    assert response.status_code == 200
    assert response.content == b"\xff\xd8frame\xff\xd9"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert stream_response.status_code == 200
    assert stream_response.content.endswith(b"frame\r\n")
    assert stream_response.headers["content-type"].startswith(
        "multipart/x-mixed-replace; boundary=frame"
    )


@pytest.mark.anyio
async def test_manual_capture_uses_all_camera_adapters_and_reports_partial_failure(
    service: SupervisorService,
):
    class SuccessfulCamera:
        node_id = "camera0"

        def capture_now(self):
            return {
                "capture_id": "capture-0",
                "captured_at": "2030-01-01T00:00:00Z",
                "terminal_event": {"event_type": "terminal.completed"},
            }

        def list_data(self, **_filters):
            return []

    class FailedCamera:
        node_id = "camera1"

        def capture_now(self):
            raise RuntimeError("camera unavailable")

    runtime = RuntimeStore(service.store.database_path)
    engine = ExecutionEngine(
        service.store,
        runtime,
        {"camera0": SuccessfulCamera(), "camera1": FailedCamera()},
    )
    app = create_app(service, api_token="secret", engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        denied = await browser.post("/api/captures/manual")
        captured = await browser.post(
            "/api/captures/manual", headers={"X-Supervisor-Token": "secret"}
        )

    assert denied.status_code == 401
    assert captured.status_code == 200
    assert captured.json()["requested"] == 2
    assert captured.json()["succeeded"] == 1
    assert captured.json()["failed"] == 1
    assert captured.json()["items"][0]["capture_id"] == "capture-0"
    assert captured.json()["failures"][0]["node_id"] == "camera1"
    assert [event.event_type for event in runtime.list_events()] == [
        "capture.manual_failed",
        "capture.manual_succeeded",
    ]


@pytest.mark.anyio
async def test_managed_camera_storage_configuration_api(service: SupervisorService):
    class ProcessManager:
        received_storage = None
        received_schedule = None
        disabled_nodes = None

        def statuses(self):
            return []

        def camera_configuration(self, node_id):
            if node_id != "camera0":
                raise ValueError("unknown node")
            return {
                "node_id": node_id,
                "camera": {"backend": "test"},
                "capture": {
                    "mode": "disabled",
                    "interval_seconds": None,
                    "daily_times": [],
                    "timezone": "Asia/Seoul",
                    "params": {},
                },
                "image": {"format": "jpeg"},
                "storages": [{"type": "file", "name": "local", "directory": "captures"}],
                "maintenance": None,
            }

        def update_storages(self, node_id, document):
            self.received_storage = document
            return {
                "node_id": node_id,
                "storages": [item.model_dump(mode="json") for item in document.items],
                "removed_retention_targets": [],
                "restarted_nodes": ["camera0"],
            }

        def update_all_storages(self, document):
            self.received_storage = document
            return {
                "scope": "common",
                "storages": [item.model_dump(mode="json") for item in document.items],
                "restarted_nodes": ["camera0"],
            }

        def apply_native_schedules(self, settings_by_node, activate):
            self.native_settings = settings_by_node
            return {
                "updated_nodes": list(settings_by_node),
                "restarted_nodes": list(settings_by_node),
                "revision": activate(),
            }

    manager = ProcessManager()
    service.apply_document(document())
    runtime = RuntimeStore(service.store.database_path)
    engine = ExecutionEngine(service.store, runtime, {})
    app = create_app(
        service, api_token="secret", engine=engine, process_manager=manager
    )
    headers = {"X-Supervisor-Token": "secret"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        assert (await browser.get("/api/nodes/camera0/configuration")).status_code == 401
        configuration = await browser.get(
            "/api/nodes/camera0/configuration", headers=headers
        )
        updated = await browser.put(
            "/api/nodes/camera0/storages",
            headers=headers,
            json={
                "items": [
                    {
                        "type": "file",
                        "name": "local",
                        "directory": "captures/camera0",
                        "layout": "daily",
                    }
                ]
            },
        )
        schedule = await browser.put(
            "/api/capture-schedules",
            headers=headers,
            json={
                "target": "camera0",
                "settings": {
                    "mode": "daily",
                    "interval_seconds": None,
                    "daily_times": ["08:30:00", "17:45:30"],
                    "timezone": "Asia/Seoul",
                },
            },
        )
        empty_schedule = await browser.put(
            "/api/capture-schedules",
            headers=headers,
            json={
                "target": "camera0",
                "settings": {
                    "mode": "daily",
                    "interval_seconds": None,
                    "daily_times": [],
                    "timezone": "Asia/Seoul",
                },
            },
        )

    assert configuration.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["restarted_nodes"] == ["camera0"]
    assert schedule.status_code == 200
    assert empty_schedule.status_code == 200
    assert empty_schedule.json()["power_plan_disabled"] is True
    assert schedule.json()["items"][0]["daily_times"] == ["08:30:00", "17:45:30"]
    assert schedule.json()["power_linked"] is True
    assert service.power_configuration().power_schedule
    assert manager.received_storage.items[0].directory.as_posix() == "captures/camera0"
    assert manager.native_settings["camera0"].mode == "disabled"
    assert service.active()["capture_schedules"][0]["mode"] == "daily"
    assert service.active()["capture_schedules"][0]["daily_times"] == []
    assert not any(
        item.activity_type == "power-keep"
        for item in service.store.active_bundle()[2].activities
    )
    event_types = [item.event_type for item in runtime.list_events(limit=20)]
    assert "configuration.capture_schedule_updated" in event_types
    assert "power.schedule_disabled" in event_types


@pytest.mark.anyio
async def test_sensor_schedule_api_persists_arbitrary_stored_times(service: SupervisorService):
    service.apply_document(document())
    runtime = RuntimeStore(service.store.database_path)
    engine = ExecutionEngine(service.store, runtime, {})
    app = create_app(service, api_token="secret", engine=engine)
    headers = {"X-Supervisor-Token": "secret"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        initial = await browser.get("/api/sensor-schedules", headers=headers)
        updated = await browser.put(
            "/api/sensor-schedules",
            headers=headers,
            json={
                "target": "environment0",
                "settings": {
                    "mode": "daily",
                    "daily_times": ["00:00:00", "00:30:00", "23:17:45"],
                    "timezone": "Asia/Seoul",
                },
            },
        )
        loaded = await browser.get("/api/sensor-schedules", headers=headers)

    assert initial.status_code == 200
    assert initial.json()["sensor_ids"] == ["environment0"]
    assert updated.status_code == 200
    assert updated.json()["items"][0]["daily_times"] == [
        "00:00:00",
        "00:30:00",
        "23:17:45",
    ]
    assert loaded.json()["items"] == service.active()["sensor_schedules"]
    assert service.active()["sensor_schedules"][0]["profile"] == "temperature-humidity"
    assert runtime.list_events()[0].event_type == "configuration.sensor_schedule_updated"


@pytest.mark.anyio
async def test_power_settings_update_warmup_cleanup_and_daily_keep(service: SupervisorService):
    service.apply_document(document())
    runtime = RuntimeStore(service.store.database_path)
    engine = ExecutionEngine(service.store, runtime, {})
    app = create_app(service, api_token="secret", engine=engine)
    headers = {"X-Supervisor-Token": "secret"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        initial = await browser.get("/api/power-settings", headers=headers)
        updated = await browser.put(
            "/api/power-settings",
            headers=headers,
            json={
                "warmup_seconds": 300,
                "cleanup_seconds": 180,
                "keep_schedules": [
                    {
                        "id": "office-hours",
                        "start": "09:00:00",
                        "end": "10:00:00",
                        "timezone": "Asia/Seoul",
                    }
                ],
            },
        )
        dashboard = await browser.get("/api/dashboard", headers=headers)

    assert initial.status_code == 200
    assert initial.json()["warmup_seconds"] == 120
    assert updated.status_code == 200
    assert updated.json()["cleanup_seconds"] == 180
    active = service.active()
    assert active["policies"]["boot-lead-seconds"] == 300
    assert active["policies"]["shutdown-grace-seconds"] == 180
    assert active["power_keep_schedules"][0]["id"] == "office-hours"
    assert any(
        item["activity_type"] == "power-keep"
        for item in dashboard.json()["active_plan"]["activities"]
    )
    assert "configuration.power_settings_updated" in [
        item.event_type for item in runtime.list_events(limit=20)
    ]
