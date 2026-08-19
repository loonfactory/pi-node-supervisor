from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pi_node_supervisor.executor import ExecutionEngine
from pi_node_supervisor.models import DataRecord
from pi_node_supervisor.runtime_store import RuntimeStore


class FakeCaptureAdapter:
    node_id = "camera0"

    def __init__(self, *, fail=False, data_fail=False):
        self.fail = fail
        self.data_fail = data_fail
        self.executions = []

    def health(self):
        return {"status": "running", "camera_connected": True}

    def execute(self, activity, run_id):
        self.executions.append((activity.activity_id, run_id))
        if self.fail:
            raise RuntimeError("camera offline")
        return {"capture_id": "cap-1", "captured_at": activity.requested_start.isoformat()}

    def list_data(self, *, activity_id=None, run_id=None, capture_id=None):
        if self.data_fail:
            raise RuntimeError("storage unavailable")
        now = datetime.now(UTC)
        return [
            DataRecord(
                data_id="camera0:local:cap-1.jpg",
                node_id="camera0",
                activity_id=activity_id,
                run_id=run_id,
                kind="image",
                captured_at=now,
                uri="http://127.0.0.1/cap-1.jpg",
                media_type="image/jpeg",
                size=100,
                integrity="available",
                summary="capture",
                indexed_at=now,
            )
        ]


def active_document(at):
    return {
        "schema_version": 1,
        "timezone": "UTC",
        "activities": [
            {
                "id": "capture",
                "type": "capture",
                "at": at.isoformat(),
                "node": "camera0",
                "profile": "default",
            }
        ],
    }


def apply_at(service, at):
    service.apply_document(active_document(at))
    return RuntimeStore(service.store.database_path)


def test_due_capture_executes_once_and_indexes_data(service):
    now = datetime(2030, 1, 1, tzinfo=UTC)
    runtime = apply_at(service, now)
    adapter = FakeCaptureAdapter()
    engine = ExecutionEngine(service.store, runtime, {"camera0": adapter})

    first = engine.tick(now)
    second = engine.tick(now)

    assert len(first) == 1
    assert first[0].status == "succeeded"
    assert second == []
    assert len(adapter.executions) == 1
    assert runtime.list_data()[0].run_id == first[0].run_id
    assert [event.event_type for event in reversed(runtime.list_events())] == [
        "run.started",
        "run.succeeded",
    ]


def test_late_one_shot_is_marked_missed_without_execution(service):
    scheduled = datetime(2030, 1, 1, tzinfo=UTC)
    runtime = apply_at(service, scheduled)
    adapter = FakeCaptureAdapter()
    engine = ExecutionEngine(service.store, runtime, {"camera0": adapter})
    result = engine.tick(scheduled + timedelta(seconds=61))
    assert result[0].status == "missed"
    assert adapter.executions == []
    assert engine.tick(scheduled + timedelta(seconds=62)) == []


def test_capture_failure_is_persisted_not_raised(service):
    now = datetime(2030, 1, 1, tzinfo=UTC)
    runtime = apply_at(service, now)
    engine = ExecutionEngine(
        service.store, runtime, {"camera0": FakeCaptureAdapter(fail=True)}
    )
    result = engine.tick(now)[0]
    assert result.status == "failed"
    assert "camera offline" in result.error
    assert runtime.list_events()[0].event_type == "run.failed"


def test_data_index_failure_does_not_reclassify_successful_capture(service):
    now = datetime(2030, 1, 1, tzinfo=UTC)
    runtime = apply_at(service, now)
    engine = ExecutionEngine(
        service.store, runtime, {"camera0": FakeCaptureAdapter(data_fail=True)}
    )
    result = engine.tick(now)[0]
    assert result.status == "succeeded"
    assert "storage unavailable" in result.result["data_index_error"]
    assert {event.event_type for event in runtime.list_events()} == {
        "run.started",
        "data.index_failed",
        "run.succeeded",
    }


def test_power_linked_recurring_capture_is_not_executed_by_supervisor(service):
    anchor = datetime(2030, 1, 1, tzinfo=UTC)
    service.apply_document(
        {
            "schema_version": 1,
            "timezone": "UTC",
            "activities": [],
            "capture_schedules": [
                {
                    "id": "camera0-interval",
                    "node": "camera0",
                    "profile": "default",
                    "mode": "interval",
                    "interval_seconds": 10,
                    "daily_times": [],
                    "timezone": "UTC",
                    "anchor_at": anchor.isoformat(),
                }
            ],
        }
    )
    runtime = RuntimeStore(service.store.database_path)
    adapter = FakeCaptureAdapter()
    engine = ExecutionEngine(
        service.store,
        runtime,
        {"camera0": adapter},
    )
    now = anchor + timedelta(seconds=35)

    first = engine.tick(now)
    second = engine.tick(now)

    assert first == []
    assert second == []
    assert adapter.executions == []
