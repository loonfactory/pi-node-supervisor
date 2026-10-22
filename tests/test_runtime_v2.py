from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pi_node_supervisor.runtime_v2 import RuntimeStoreV2


def test_legacy_database_is_discarded(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sensor_deliveries(id INTEGER)")

    store = RuntimeStoreV2(database)

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert "sensor_deliveries" not in tables
    assert "storage_results" in tables
    assert version == 2


def test_running_work_becomes_interrupted_without_replay(tmp_path: Path) -> None:
    store = RuntimeStoreV2(tmp_path / "runtime.db")
    scheduled_at = datetime(2030, 1, 1, 3, 0, tzinfo=UTC)
    claimed = store.claim_run(
        revision_id=1,
        device_id="rgb",
        device_kind="camera",
        scheduled_at=scheduled_at,
    )
    assert claimed is not None

    recovered = store.recover_interrupted()
    duplicate = store.claim_run(
        revision_id=1,
        device_id="rgb",
        device_kind="camera",
        scheduled_at=scheduled_at,
    )

    assert recovered[0].status == "interrupted"
    assert duplicate is None
    assert store.list_events()[0].event_type == "run.interrupted"


def test_acknowledged_failures_leave_history_and_only_count_new_failures(
    tmp_path: Path,
) -> None:
    store = RuntimeStoreV2(tmp_path / "runtime.db")
    first = store.claim_run(
        revision_id=1,
        device_id="rgb",
        device_kind="camera",
        scheduled_at=datetime(2030, 1, 1, 3, 0, tzinfo=UTC),
    )
    assert first is not None
    store.complete_run(first.run_id, status="failed", error="first failure")

    assert store.counts()["failedRuns"] == 1
    assert store.acknowledge_failed_runs() == 1
    assert store.counts()["failedRuns"] == 0
    assert store.counts()["totalFailedRuns"] == 1
    assert store.get_run(first.run_id).error == "first failure"

    second = store.claim_run(
        revision_id=1,
        device_id="noir",
        device_kind="camera",
        scheduled_at=datetime(2030, 1, 1, 3, 1, tzinfo=UTC),
    )
    assert second is not None
    store.complete_run(second.run_id, status="interrupted", error="new interruption")

    assert store.counts()["failedRuns"] == 1
    assert store.counts()["totalFailedRuns"] == 2
    assert len(store.list_runs()) == 2
