import asyncio
from datetime import UTC, datetime

import pytest

from pi_node_supervisor.config_v2 import resolve_configuration
from pi_node_supervisor.executor_v2 import ExecutionEngineV2
from pi_node_supervisor.runtime_v2 import RuntimeStoreV2


@pytest.mark.parametrize("skew,fail,status", [(2_000_000, False, "aligned"), (50_000_000, False, "excessiveSkew"), (0, True, "incomplete")])
@pytest.mark.parametrize("scheduled", [False, True])
def test_camera_batch_uses_common_target_and_records_actual_skew(tmp_path, skew, fail, status, scheduled):
    document = {
        "schemaVersion": 2,
        "schedules": {"daily": [{"type": "daily", "time": "12:00:00"}]},
        "cameras": {
            "defaults": {"schedule": "daily"},
            "devices": {
                "noir": {"captureNode": {"camera": {"type": "picamera2", "cameraNum": 0}}},
                "rgb": {"captureNode": {"camera": {"type": "picamera2", "cameraNum": 1}}},
            },
        },
    }
    configuration = resolve_configuration(document).resolved
    runtime = RuntimeStoreV2(tmp_path / "runtime.db")
    targets = {}

    class Worker:
        def execute(self, run, *, capture_at_ns, parameters=None):
            targets[run.device_id] = capture_at_ns
            return runtime.complete_run(
                run.run_id,
                status="failed" if fail and run.device_id == "rgb" else "succeeded",
                result={
                    "sensorTimestampNs": capture_at_ns + 1_000_000 + (skew if run.device_id == "rgb" else 0),
                    "syncReady": True,
                    "captureAtNs": capture_at_ns,
                },
            )

    engine = ExecutionEngineV2(configuration, 1, runtime, {"noir": Worker(), "rgb": Worker()}, {}, local_timezone=UTC)
    if scheduled:
        runs = asyncio.run(engine.tick(datetime(2030, 1, 1, 12, tzinfo=UTC)))
    else:
        runs = asyncio.run(engine.capture_all())
    assert len(runs) == 2
    assert targets["noir"] == targets["rgb"]
    event = next(item for item in runtime.list_events() if item.event_type == "camera.synchronization")
    assert event.payload["status"] == status
    assert event.payload["skewMilliseconds"] == (None if fail else skew / 1e6)
    assert event.severity == ("info" if status == "aligned" else "warning")
