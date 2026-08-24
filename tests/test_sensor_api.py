from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from pi_node_supervisor.api import create_app
from pi_node_supervisor.executor import ExecutionEngine
from pi_node_supervisor.models import SensorReading
from pi_node_supervisor.registry import NodeRegistry
from pi_node_supervisor.runtime_store import RuntimeStore
from pi_node_supervisor.sensor_adapter import CwtSensorAdapter
from pi_node_supervisor.sensor_storage import SensorStorageCoordinator
from pi_node_supervisor.service import SupervisorService
from pi_node_supervisor.storage import RevisionStore


def sensor_registry(path: Path, local_directory: Path) -> NodeRegistry:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "nodes": [
                    {
                        "id": "environment0",
                        "kind": "sensor-node",
                        "activity_types": ["sensor-collection"],
                        "profiles": ["temperature-humidity"],
                        "default_runtime_seconds": 5,
                        "sensor_serial": {
                            "driver": "cwt-soil-thc-s",
                            "port": "/dev/ttyAMA0",
                            "baudrate": 4800,
                            "timeout_seconds": 1,
                            "slave_id": 1,
                        },
                        "sensor_storage": {
                            "local_directory": str(local_directory),
                            "retry_base_seconds": 1,
                            "retry_max_seconds": 1,
                            "mirrors": [
                                {
                                    "type": "sftp",
                                    "name": "sftp-copy",
                                    "host": "sftp.example.local",
                                    "username": "sensor",
                                    "remote_directory": "/archive",
                                    "password_env": "SENSOR_SFTP_PASSWORD",
                                },
                                {
                                    "type": "ftps",
                                    "name": "ftps-copy",
                                    "host": "ftps.example.local",
                                    "username": "sensor",
                                    "remote_directory": "/archive",
                                    "password_env": "SENSOR_FTPS_PASSWORD",
                                },
                            ],
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return NodeRegistry.load(path)


def sensor_configuration() -> dict:
    return {
        "schema_version": 1,
        "timezone": "UTC",
        "policies": {
            "boot-lead-seconds": 0,
            "shutdown-grace-seconds": 0,
            "merge-power-gaps-shorter-than-seconds": 300,
            "minimum-lead-seconds": 1,
            "clock-tolerance-seconds": 5,
        },
        "activities": [],
        "sensor_schedules": [
            {
                "id": "environment0-temperature-humidity",
                "node": "environment0",
                "profile": "temperature-humidity",
                "mode": "daily",
                "daily_times": ["00:00:00", "23:30:00"],
                "timezone": "UTC",
            }
        ],
    }


class Provider:
    def __init__(self, clock: dict[str, datetime]) -> None:
        self.clock = clock
        self.reads = 0

    def health(self):
        return {"serial_open": True}

    def read(self):
        self.reads += 1
        return SensorReading(
            observed_at=self.clock["now"],
            temperature_c=20 + self.reads,
            humidity_percent=60 + self.reads,
        )

    def close(self):
        pass


class Mirror:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.uploads: list[tuple[bytes, str]] = []

    def upload_atomic(self, local_path, relative_path):
        if self.fail:
            raise OSError(f"{self.name} unavailable")
        self.uploads.append((local_path.read_bytes(), str(relative_path)))


@pytest.mark.anyio
async def test_sensor_settings_are_validated_and_staged_atomically(tmp_path: Path):
    nodes_path = tmp_path / "nodes.json"
    registry = sensor_registry(nodes_path, tmp_path / "sensors")
    original = nodes_path.read_bytes()
    service = SupervisorService(
        registry,
        RevisionStore(tmp_path / "state"),
        active_configuration_path=tmp_path / "config.json",
        power_manager_configuration_path=tmp_path / "power.json",
    )
    app = create_app(service, api_token="secret")
    headers = {"X-Supervisor-Token": "secret"}
    document = {
        "sensor_serial": {
            "driver": "cwt-soil-thc-s",
            "port": "/dev/serial/by-id/cwt-sensor-01",
            "baudrate": 9600,
            "timeout_seconds": 2,
            "slave_id": 7,
        },
        "sensor_storage": {
            "local_directory": str(tmp_path / "new-sensors"),
            "mirrors": [],
            "retry_base_seconds": 30,
            "retry_max_seconds": 300,
        },
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        invalid = await browser.put(
            "/api/sensor-settings/environment0",
            headers=headers,
            json={**document, "sensor_serial": {**document["sensor_serial"], "port": "/tmp/tty0"}},
        )
        updated = await browser.put(
            "/api/sensor-settings/environment0", headers=headers, json=document
        )
        loaded = await browser.get("/api/sensor-settings", headers=headers)

    assert invalid.status_code == 422
    assert updated.status_code == 200
    assert updated.json()["restart_required"] is True
    assert loaded.json()["restart_required"] is True
    assert loaded.json()["items"][0]["profile"] == "temperature-humidity"
    assert loaded.json()["items"][0]["sensor_serial"]["port"].startswith("/dev/")
    assert nodes_path.with_name("nodes.json.previous").read_bytes() == original
    assert json.loads(nodes_path.read_text(encoding="utf-8"))["nodes"][0][
        "sensor_storage"
    ]["local_directory"] == str(tmp_path / "new-sensors")


@pytest.mark.anyio
async def test_sensor_http_flow_preserves_boundary_rows_and_recovers_mirrors(tmp_path: Path):
    registry = sensor_registry(tmp_path / "nodes.json", tmp_path / "sensors")
    service = SupervisorService(
        registry,
        RevisionStore(tmp_path / "state"),
        active_configuration_path=tmp_path / "config.json",
        power_manager_configuration_path=tmp_path / "power.json",
    )
    service.apply_document(sensor_configuration())
    runtime = RuntimeStore(service.store.database_path)
    clock = {"now": datetime.now(UTC)}
    provider = Provider(clock)
    sftp = Mirror("sftp-copy")
    ftps = Mirror("ftps-copy", fail=True)
    definition = registry.require("environment0")
    coordinator = SensorStorageCoordinator(
        node_id=definition.id,
        timezone="UTC",
        settings=definition.sensor_storage,
        runtime=runtime,
        mirrors={sftp.name: sftp, ftps.name: ftps},
        clock=lambda: clock["now"],
    )
    adapter = CwtSensorAdapter(definition, provider, coordinator)
    engine = ExecutionEngine(
        service.store,
        runtime,
        {definition.id: adapter},
        clock=lambda: clock["now"],
    )
    app = create_app(service, api_token="secret", engine=engine)
    headers = {"X-Supervisor-Token": "secret"}
    occurrences = sorted(
        item.requested_start
        for item in service.store.active_bundle()[2].activities
        if item.activity_type == "sensor-collection"
    )
    boundary = []
    for occurrence in occurrences:
        if occurrence.strftime("%H:%M") in {"00:00", "23:30"}:
            if not boundary or occurrence.date() != boundary[-1].date():
                boundary.append(occurrence)
        if len(boundary) == 2:
            break
    assert [item.strftime("%H:%M") for item in boundary] == ["23:30", "00:00"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as browser:
        for occurrence in boundary:
            clock["now"] = occurrence
            assert len(engine.tick()) == 1
        duplicate = engine.tick()
        failed_status = await browser.get("/api/sensor-storage", headers=headers)
        before_retry = await browser.get("/api/sensor-data", headers=headers)
        clock["now"] = boundary[-1] + timedelta(seconds=2)
        ftps.fail = False
        retried = await browser.post("/api/sensor-storage/sync", headers=headers)
        after_retry = await browser.get("/api/sensor-data", headers=headers)

    assert duplicate == []
    assert provider.reads == 2
    assert failed_status.json()["counts"]["failed"] == 2
    assert before_retry.json()["total"] == 2
    assert {item["sync_status"] for item in before_retry.json()["items"]} == {"failed"}
    assert retried.json()["counts"] == {"pending": 0, "failed": 0, "succeeded": 4}
    rows = after_retry.json()["items"]
    assert [item["timestamp"][11:16] for item in rows] == ["00:00", "23:30"]
    assert all(set(item) >= {"timestamp", "temperature_c", "humidity_percent"} for item in rows)
    assert {item["sync_status"] for item in rows} == {"synchronized"}
    assert len(sftp.uploads) == 2
    assert len(ftps.uploads) == 2
