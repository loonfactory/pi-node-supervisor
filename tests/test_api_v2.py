from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from pi_node_supervisor.api_v2 import create_app_v2
from pi_node_supervisor.application_v2 import RuntimeControllerV2
from pi_node_supervisor.revision_v2 import RevisionStoreV2
from pi_node_supervisor.service_v2 import SupervisorServiceV2


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
