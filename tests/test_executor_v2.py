from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timezone
from pathlib import Path

from pi_node_supervisor.capture_v2 import StagedArtifact, StagedCaptureBundle
from pi_node_supervisor.config_v2 import ResolvedCamera, resolve_configuration
from pi_node_supervisor.executor_v2 import CameraWorker, ExecutionEngineV2
from pi_node_supervisor.runtime_v2 import ActiveWork, RuntimeStoreV2
from pi_node_supervisor.storage_v2 import SaveResult, StoragePipeline


class FakeClient:
    def __init__(self, bundle):
        self.bundle = bundle

    def health(self):
        return {"status": "running", "cameraConnected": True}

    def capture(self, _request_id, *, parameters):
        return self.bundle


class FailedStorage:
    name = "nas"

    def save_atomic(self, _source, relative):
        raise OSError(f"cannot store {relative}")


def test_manual_sensor_collection_creates_run_and_executes_worker(
    tmp_path: Path,
) -> None:
    runtime = RuntimeStoreV2(tmp_path / "runtime.db")

    class SensorWorker:
        async def execute(self, run):
            return runtime.complete_run(
                run.run_id,
                status="succeeded",
                result={"observedAt": datetime.now(UTC).isoformat()},
            )

    configuration = resolve_configuration(
        {
            "schemaVersion": 2,
            "sensors": {
                "devices": {
                    "soil": {
                        "driver": {
                            "name": "cwt_soil_thc_s",
                            "port": "/dev/ttyAMA0",
                            "unitId": 1,
                        }
                    }
                }
            },
            "power": {"minOffSeconds": 300},
        }
    ).resolved
    engine = ExecutionEngineV2(
        configuration,
        1,
        runtime,
        {},
        {"soil": SensorWorker()},
        local_timezone=timezone.utc,
    )

    completed = asyncio.run(engine.collect_now("soil"))

    assert completed.status == "succeeded"
    assert completed.device_kind == "sensor"
    assert completed.trigger == "manual"


def test_capture_run_finishes_after_storage_attempt_even_when_target_fails(
    tmp_path: Path,
) -> None:
    runtime = RuntimeStoreV2(tmp_path / "runtime.db")
    active = ActiveWork()
    staged = tmp_path / "staging"
    staged.mkdir()
    image = staged / "capture.dng"
    image.write_bytes(b"raw")
    bundle = StagedCaptureBundle(
        request_id="unused",
        capture_id="capture-001",
        captured_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
        directory=staged,
        artifacts=(
            StagedArtifact(
                name="capture.dng",
                media_type="image/x-adobe-dng",
                path=image,
                size=3,
                sha256=hashlib.sha256(b"raw").hexdigest(),
            ),
        ),
    )
    run = runtime.claim_run(
        revision_id=1,
        device_id="rgb",
        device_kind="camera",
        scheduled_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
    )
    assert run is not None
    worker = CameraWorker(
        ResolvedCamera(
            device_id="rgb",
            capture_node={},
            schedule_name=None,
            schedule=(),
            storages=(),
        ),
        FakeClient(bundle),
        StoragePipeline((FailedStorage(),)),
        runtime,
        active,
        local_timezone=timezone.utc,
    )

    completed = worker.execute(run)

    assert completed.status == "succeeded"
    assert completed.result["storageSucceeded"] == 0
    assert completed.result["storageAttempted"] == 1
    failure = runtime.list_storage_results(run.run_id)[0]
    assert failure["target"] == "nas"
    assert failure["succeeded"] is False
    event = runtime.list_events()[0]
    assert event.event_type == "run.succeeded"
    storage_event = runtime.list_events()[1]
    assert storage_event.event_type == "storage.failed"
    assert storage_event.payload["target"] == "nas"
    assert active.count == 0
    assert not staged.exists()


def test_shutdown_rejection_finishes_claimed_camera_run_as_failed(
    tmp_path: Path,
) -> None:
    runtime = RuntimeStoreV2(tmp_path / "runtime.db")
    active = ActiveWork()
    assert active.begin_shutdown_if_idle()
    run = runtime.claim_run(
        revision_id=1,
        device_id="rgb",
        device_kind="camera",
        scheduled_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
        trigger="manual",
    )
    assert run is not None
    worker = CameraWorker(
        ResolvedCamera(
            device_id="rgb",
            capture_node={},
            schedule_name=None,
            schedule=(),
            storages=(),
        ),
        FakeClient(None),
        StoragePipeline(()),
        runtime,
        active,
        local_timezone=timezone.utc,
    )

    completed = worker.execute(run)

    assert completed.status == "failed"
    assert "shutdown is being prepared" in completed.error
    assert runtime.counts()["running"] == 0
