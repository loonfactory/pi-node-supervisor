from __future__ import annotations

from datetime import UTC, datetime

from pi_node_supervisor.models import DataRecord
from pi_node_supervisor.runtime_store import RuntimeStore


def test_run_claim_is_idempotent_per_revision_and_activity(tmp_path):
    store = RuntimeStore(tmp_path / "state.db")
    scheduled = datetime(2030, 1, 1, tzinfo=UTC)
    first = store.claim_run(
        revision_id=1,
        activity_id="capture",
        node_id="camera0",
        scheduled_at=scheduled,
    )
    second = store.claim_run(
        revision_id=1,
        activity_id="capture",
        node_id="camera0",
        scheduled_at=scheduled,
    )
    assert first is not None
    assert second is None
    completed = store.complete_run(first.run_id, status="succeeded", external_id="cap-1")
    assert completed.status == "succeeded"
    assert store.find_run_by_external_id("camera0", "cap-1") == completed
    assert store.find_run_by_external_id("camera1", "cap-1") is None


def test_restart_recovery_marks_running_without_replay(tmp_path):
    store = RuntimeStore(tmp_path / "state.db")
    run = store.claim_run(
        revision_id=1,
        activity_id="capture",
        node_id="camera0",
        scheduled_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert run is not None
    recovered = store.recover_interrupted()
    assert [item.run_id for item in recovered] == [run.run_id]
    assert recovered[0].status == "interrupted"
    assert store.recover_interrupted() == []
    event = store.list_events()[0]
    assert event.event_type == "run.interrupted"
    assert event.payload["replayed"] is False
    assert store.count_events() == 1


def test_data_catalog_upsert_preserves_run_link_on_later_rescan(tmp_path):
    store = RuntimeStore(tmp_path / "state.db")
    now = datetime(2030, 1, 1, tzinfo=UTC)
    linked = DataRecord(
        data_id="camera0:local:image.jpg",
        node_id="camera0",
        activity_id="capture",
        run_id="run-1",
        kind="image",
        captured_at=now,
        uri="http://127.0.0.1/image.jpg",
        media_type="image/jpeg",
        size=10,
        integrity="available",
        summary="first",
        indexed_at=now,
    )
    rescan = linked.model_copy(
        update={"activity_id": None, "run_id": None, "summary": "rescanned", "size": 12}
    )
    store.upsert_data(linked)
    store.upsert_data(rescan)
    record = store.list_data()[0]
    assert record.run_id == "run-1"
    assert record.activity_id == "capture"
    assert record.summary == "rescanned"
    assert record.size == 12
    assert store.get_data(linked.data_id) == record
    assert store.get_data("missing") is None
    assert store.count_data() == 1
