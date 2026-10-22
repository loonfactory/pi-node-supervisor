from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from pi_node_supervisor.api_v2 import create_app_v2
from pi_node_supervisor.preview_stream import chunked_body, parse_preview_headers
from pi_node_supervisor.application_v2 import RuntimeControllerV2
from pi_node_supervisor.revision_v2 import RevisionStoreV2
from pi_node_supervisor.service_v2 import SupervisorServiceV2


def test_preview_proxy_decodes_upstream_chunked_stream() -> None:
    media_type, chunked = parse_preview_headers(
        b"HTTP/1.1 200 OK\r\n"
        b"content-type: multipart/x-mixed-replace; boundary=frame\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: close\r\n\r\n"
    )

    assert media_type == "multipart/x-mixed-replace; boundary=frame"
    assert chunked is True


def test_preview_proxy_relays_chunk_body_without_http_framing() -> None:
    async def relay() -> bytes:
        reader = asyncio.StreamReader()
        reader.feed_data(b"4\r\npart\r\n4\r\n-two\r\n0\r\n\r\n")
        reader.feed_eof()
        return b"".join([chunk async for chunk in chunked_body(reader)])

    assert asyncio.run(relay()) == b"part-two"


def test_v2_config_workflow_and_removed_sync_routes(tmp_path: Path) -> None:
    service = SupervisorServiceV2(
        RevisionStoreV2(tmp_path / "state"),
        active_configuration_path=tmp_path / "etc/config.json",
        generated_capture_directory=tmp_path / "generated",
        power_configuration_path=tmp_path / "power/config.json",
        local_timezone=timezone(timedelta(hours=9)),
        timezone_name="Asia/Seoul",
    )
    controller = RuntimeControllerV2(
        service,
        capture_node_executable=None,
        staging_directory=tmp_path / "staging",
    )
    app = create_app_v2(
        service,
        controller,
        api_token="secret",
        manage_runtime=False,
        static_directory=tmp_path / "missing",
    )
    headers = {"Authorization": "Bearer secret"}
    document = {
        "schemaVersion": 2,
        "power": {"minOffSeconds": 300},
    }

    async def exercise_api():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://supervisor.test"
        ) as client:
            return (
                await client.put("/api/config/draft", headers=headers, json=document),
                await client.post("/api/revisions/apply", headers=headers),
                await client.get("/api/config/active", headers=headers),
                await client.post("/api/sensor-storage/sync", headers=headers),
            )

    saved, applied, active, removed = asyncio.run(exercise_api())

    assert saved.status_code == 200
    assert applied.status_code == 200
    assert active.json()["schemaVersion"] == 2
    assert removed.status_code == 404


def test_camera_preview_token_cookie_auth_and_stream_relay(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pi_node_supervisor import api_auth, preview_api

    payload = (
        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n"
        b"jpeg\r\n--frame--\r\n"
    )
    closed = []

    class Writer:
        def close(self):
            closed.append(True)

        async def wait_closed(self):
            pass

    async def open_stream(endpoint):
        assert endpoint == "http://127.0.0.1:8001"
        reader = asyncio.StreamReader()
        reader.feed_data(payload)
        reader.feed_eof()
        return reader, Writer(), "multipart/x-mixed-replace; boundary=frame", False

    monkeypatch.setattr(preview_api, "open_preview_stream", open_stream)
    now = 1_800_000_000
    monkeypatch.setattr(api_auth.time, "time", lambda: now)
    controller = SimpleNamespace(
        process_manager=SimpleNamespace(endpoints={"cam1": "http://127.0.0.1:8001"})
    )
    app = create_app_v2(
        None, controller, api_token="secret", manage_runtime=False,
        static_directory=tmp_path / "missing",
    )

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://supervisor.test"
        ) as client:
            assert (await client.get("/api/camera/cam1")).status_code == 401
            assert (await client.get(
                "/api/camera/cam1", headers={"Authorization": "Bearer wrong"}
            )).status_code == 401
            direct = await client.get(
                "/api/camera/cam1", headers={"Authorization": "Bearer secret"}
            )
            assert direct.status_code == 200
            assert direct.content == payload
            assert closed == [True]
            closed.clear()
            assert (await client.post("/api/camera-session")).status_code == 401
            session = await client.post(
                "/api/camera-session", headers={"Authorization": "Bearer secret"}
            )
            assert session.status_code == 204
            cookie = session.headers["set-cookie"]
            for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api/camera/"):
                assert attribute in cookie
            assert "secret" not in cookie
            response = await client.get("/api/camera/cam1")
            assert response.status_code == 200
            assert response.content == payload
            assert response.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
            assert response.headers["cache-control"] == "no-store"
            assert closed == [True]
            # A valid cookie must not mask invalid explicit credentials.
            assert (await client.get(
                "/api/camera/cam1", headers={"Authorization": "Bearer wrong"}
            )).status_code == 401
            assert (await client.get("/api/camera/missing")).status_code == 404
            assert (await client.get("/api/dashboard")).status_code == 401
            assert (await client.get(
                "/api/camera/cam1", headers={"Cookie": "supervisor-preview=invalid"}
            )).status_code == 401
            monkeypatch.setattr(api_auth.time, "time", lambda: now + 8 * 60 * 60 + 1)
            assert (await client.get("/api/camera/cam1")).status_code == 401
            # Token clients remain independent of browser session expiration.
            direct = await client.get(
                "/api/camera/cam1", headers={"Authorization": "Bearer secret"}
            )
            assert direct.status_code == 200
            assert direct.content == payload

    asyncio.run(exercise())
