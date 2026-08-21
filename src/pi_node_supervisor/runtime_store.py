"""SQLite persistence for runs, events, and node-owned data metadata."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import PersistenceError
from .models import DataRecord, EventRecord, RunRecord
from .timeutil import format_utc


def _now() -> datetime:
    return datetime.now(UTC)


class RuntimeStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        revision_id INTEGER NOT NULL,
                        activity_id TEXT NOT NULL,
                        node_id TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed', 'missed', 'interrupted')),
                        scheduled_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        external_id TEXT,
                        result_json TEXT,
                        error TEXT,
                        UNIQUE(revision_id, activity_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
                    CREATE INDEX IF NOT EXISTS idx_runs_scheduled ON runs(scheduled_at DESC);

                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        severity TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'error')),
                        run_id TEXT,
                        activity_id TEXT,
                        node_id TEXT,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at DESC, id DESC);
                    CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);

                    CREATE TABLE IF NOT EXISTS data_catalog (
                        data_id TEXT PRIMARY KEY,
                        node_id TEXT NOT NULL,
                        activity_id TEXT,
                        run_id TEXT,
                        kind TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        uri TEXT NOT NULL,
                        media_type TEXT,
                        size INTEGER,
                        integrity TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        indexed_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_data_captured ON data_catalog(captured_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_data_run ON data_catalog(run_id);

                    CREATE TABLE IF NOT EXISTS sensor_deliveries (
                        node_id TEXT NOT NULL,
                        target TEXT NOT NULL,
                        relative_path TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('pending', 'succeeded', 'failed')),
                        attempts INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at TEXT,
                        last_error TEXT,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(node_id, target, relative_path)
                    );
                    CREATE INDEX IF NOT EXISTS idx_sensor_delivery_due
                    ON sensor_deliveries(status, next_attempt_at);
                    """
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot initialize runtime database: {exc}") from exc

    def claim_run(
        self,
        *,
        revision_id: int,
        activity_id: str,
        node_id: str,
        scheduled_at: datetime,
        status: str = "running",
        error: str | None = None,
    ) -> RunRecord | None:
        run_id = uuid.uuid4().hex
        now = _now()
        started_at = now if status == "running" else None
        completed_at = None if status == "running" else now
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO runs
                        (run_id, revision_id, activity_id, node_id, status,
                         scheduled_at, started_at, completed_at, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        revision_id,
                        activity_id,
                        node_id,
                        status,
                        format_utc(scheduled_at),
                        format_utc(started_at) if started_at else None,
                        format_utc(completed_at) if completed_at else None,
                        error,
                    ),
                )
                if cursor.rowcount == 0:
                    return None
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot claim activity run: {exc}") from exc
        return self.get_run(run_id)

    def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        external_id: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> RunRecord:
        if status not in {"succeeded", "failed", "interrupted"}:
            raise ValueError(f"invalid terminal run status: {status}")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runs
                    SET status = ?, completed_at = ?, external_id = ?, result_json = ?, error = ?
                    WHERE run_id = ? AND status = 'running'
                    """,
                    (
                        status,
                        format_utc(_now()),
                        external_id,
                        json.dumps(result, ensure_ascii=False, sort_keys=True) if result else None,
                        error,
                        run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PersistenceError(f"run is not active: {run_id}")
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot complete run: {exc}") from exc
        return self.get_run(run_id)

    def recover_interrupted(self) -> list[RunRecord]:
        recovered: list[RunRecord] = []
        now = _now()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT run_id, activity_id, node_id FROM runs WHERE status = 'running'"
                ).fetchall()
                for row in rows:
                    reason = "supervisor restarted while the run was active; automatic replay is disabled"
                    connection.execute(
                        """
                        UPDATE runs SET status = 'interrupted', completed_at = ?, error = ?
                        WHERE run_id = ? AND status = 'running'
                        """,
                        (format_utc(now), reason, row["run_id"]),
                    )
                    self._insert_event(
                        connection,
                        event_type="run.interrupted",
                        severity="warning",
                        run_id=row["run_id"],
                        activity_id=row["activity_id"],
                        node_id=row["node_id"],
                        payload={"reason": reason, "replayed": False},
                        occurred_at=now,
                    )
            recovered = [self.get_run(row["run_id"]) for row in rows]
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot recover interrupted runs: {exc}") from exc
        return recovered

    def get_run(self, run_id: str) -> RunRecord:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read run: {exc}") from exc
        if row is None:
            raise PersistenceError(f"run does not exist: {run_id}")
        return self._run_record(row)

    def find_run_by_external_id(
        self, node_id: str, external_id: str
    ) -> RunRecord | None:
        """Return an imported node-owned run without tying it to a revision."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM runs
                    WHERE node_id = ? AND external_id = ?
                    ORDER BY scheduled_at DESC, run_id DESC
                    LIMIT 1
                    """,
                    (node_id, external_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read external run: {exc}") from exc
        return self._run_record(row) if row is not None else None

    def list_runs(self, *, limit: int = 100, offset: int = 0) -> list[RunRecord]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid run pagination")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM runs ORDER BY scheduled_at DESC, run_id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot list runs: {exc}") from exc
        return [self._run_record(row) for row in rows]

    def append_event(
        self,
        event_type: str,
        *,
        severity: str = "info",
        run_id: str | None = None,
        activity_id: str | None = None,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        occurred_at = _now()
        try:
            with self._connect() as connection:
                event_id = self._insert_event(
                    connection,
                    event_type=event_type,
                    severity=severity,
                    run_id=run_id,
                    activity_id=activity_id,
                    node_id=node_id,
                    payload=payload or {},
                    occurred_at=occurred_at,
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot append event: {exc}") from exc
        return EventRecord(
            id=event_id,
            occurred_at=occurred_at,
            event_type=event_type,
            severity=severity,
            run_id=run_id,
            activity_id=activity_id,
            node_id=node_id,
            payload=payload or {},
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        event_type: str,
        severity: str,
        run_id: str | None,
        activity_id: str | None,
        node_id: str | None,
        payload: dict[str, Any],
        occurred_at: datetime,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO events
                (occurred_at, event_type, severity, run_id, activity_id, node_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                format_utc(occurred_at),
                event_type,
                severity,
                run_id,
                activity_id,
                node_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        return int(cursor.lastrowid)

    def list_events(self, *, limit: int = 100, offset: int = 0) -> list[EventRecord]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid event pagination")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM events ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot list events: {exc}") from exc
        return [
            EventRecord(
                id=row["id"],
                occurred_at=row["occurred_at"],
                event_type=row["event_type"],
                severity=row["severity"],
                run_id=row["run_id"],
                activity_id=row["activity_id"],
                node_id=row["node_id"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def count_events(self) -> int:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot count events: {exc}") from exc
        return int(row["count"])

    def upsert_data(self, record: DataRecord) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO data_catalog
                        (data_id, node_id, activity_id, run_id, kind, captured_at,
                         uri, media_type, size, integrity, summary, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(data_id) DO UPDATE SET
                        activity_id = COALESCE(excluded.activity_id, data_catalog.activity_id),
                        run_id = COALESCE(excluded.run_id, data_catalog.run_id),
                        captured_at = excluded.captured_at,
                        uri = excluded.uri,
                        media_type = excluded.media_type,
                        size = excluded.size,
                        integrity = excluded.integrity,
                        summary = excluded.summary,
                        indexed_at = excluded.indexed_at
                    """,
                    (
                        record.data_id,
                        record.node_id,
                        record.activity_id,
                        record.run_id,
                        record.kind,
                        format_utc(record.captured_at),
                        record.uri,
                        record.media_type,
                        record.size,
                        record.integrity,
                        record.summary,
                        format_utc(record.indexed_at),
                    ),
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot index data record: {exc}") from exc

    def list_data(self, *, limit: int = 100, offset: int = 0) -> list[DataRecord]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid data pagination")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM data_catalog ORDER BY captured_at DESC, data_id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot list data: {exc}") from exc
        return [DataRecord.model_validate(dict(row)) for row in rows]

    def get_data(self, data_id: str) -> DataRecord | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM data_catalog WHERE data_id = ?", (data_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read data record: {exc}") from exc
        return DataRecord.model_validate(dict(row)) if row is not None else None

    def count_data(self) -> int:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT COUNT(*) AS count FROM data_catalog").fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot count data records: {exc}") from exc
        return int(row["count"])

    def counts(self) -> dict[str, int]:
        try:
            with self._connect() as connection:
                return {
                    "runs": connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                    "failed_runs": connection.execute(
                        "SELECT COUNT(*) FROM runs WHERE status IN ('failed', 'interrupted', 'missed')"
                    ).fetchone()[0],
                    "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    "data": connection.execute("SELECT COUNT(*) FROM data_catalog").fetchone()[0],
                    "sensor_delivery_backlog": connection.execute(
                        "SELECT COUNT(*) FROM sensor_deliveries WHERE status != 'succeeded'"
                    ).fetchone()[0],
                }
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot count runtime records: {exc}") from exc

    def queue_sensor_delivery(
        self, *, node_id: str, target: str, relative_path: str, content_sha256: str
    ) -> None:
        now = format_utc(_now())
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO sensor_deliveries
                        (node_id, target, relative_path, content_sha256, status,
                         attempts, next_attempt_at, last_error, updated_at)
                    VALUES (?, ?, ?, ?, 'pending', 0, ?, NULL, ?)
                    ON CONFLICT(node_id, target, relative_path) DO UPDATE SET
                        content_sha256 = excluded.content_sha256,
                        status = CASE WHEN sensor_deliveries.content_sha256 = excluded.content_sha256
                                      AND sensor_deliveries.status = 'succeeded'
                                      THEN 'succeeded' ELSE 'pending' END,
                        attempts = CASE WHEN sensor_deliveries.content_sha256 = excluded.content_sha256
                                       THEN sensor_deliveries.attempts ELSE 0 END,
                        next_attempt_at = CASE WHEN sensor_deliveries.content_sha256 = excluded.content_sha256
                                               AND sensor_deliveries.status = 'succeeded'
                                               THEN NULL ELSE excluded.next_attempt_at END,
                        last_error = CASE WHEN sensor_deliveries.content_sha256 = excluded.content_sha256
                                          THEN sensor_deliveries.last_error ELSE NULL END,
                        updated_at = excluded.updated_at
                    """,
                    (node_id, target, relative_path, content_sha256, now, now),
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot queue sensor delivery: {exc}") from exc

    def due_sensor_deliveries(self, now: datetime) -> list[dict[str, Any]]:
        value = format_utc(now)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM sensor_deliveries
                    WHERE status != 'succeeded'
                      AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                    ORDER BY updated_at, node_id, target, relative_path
                    """,
                    (value,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read sensor delivery backlog: {exc}") from exc
        return [dict(row) for row in rows]

    def complete_sensor_delivery(
        self, *, node_id: str, target: str, relative_path: str
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE sensor_deliveries
                    SET status = 'succeeded', next_attempt_at = NULL,
                        last_error = NULL, updated_at = ?
                    WHERE node_id = ? AND target = ? AND relative_path = ?
                    """,
                    (format_utc(_now()), node_id, target, relative_path),
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot complete sensor delivery: {exc}") from exc

    def fail_sensor_delivery(
        self,
        *,
        node_id: str,
        target: str,
        relative_path: str,
        error: str,
        retry_base_seconds: int,
        retry_max_seconds: int,
        now: datetime,
    ) -> None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """SELECT attempts FROM sensor_deliveries
                       WHERE node_id = ? AND target = ? AND relative_path = ?""",
                    (node_id, target, relative_path),
                ).fetchone()
                attempts = (int(row["attempts"]) if row else 0) + 1
                delay = min(
                    retry_max_seconds,
                    retry_base_seconds * (2 ** min(attempts - 1, 30)),
                )
                connection.execute(
                    """
                    UPDATE sensor_deliveries
                    SET status = 'failed', attempts = ?, next_attempt_at = ?,
                        last_error = ?, updated_at = ?
                    WHERE node_id = ? AND target = ? AND relative_path = ?
                    """,
                    (
                        attempts,
                        format_utc(now + timedelta(seconds=delay)),
                        error[:2000],
                        format_utc(now),
                        node_id,
                        target,
                        relative_path,
                    ),
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot fail sensor delivery: {exc}") from exc

    def sensor_delivery_status(self) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT status, COUNT(*) AS count FROM sensor_deliveries
                       GROUP BY status"""
                ).fetchall()
                recent = connection.execute(
                    """SELECT * FROM sensor_deliveries
                       ORDER BY updated_at DESC LIMIT 100"""
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read sensor delivery status: {exc}") from exc
        counts = {row["status"]: row["count"] for row in rows}
        return {
            "counts": {key: counts.get(key, 0) for key in ("pending", "failed", "succeeded")},
            "items": [dict(row) for row in recent],
        }

    @staticmethod
    def _run_record(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            revision_id=row["revision_id"],
            activity_id=row["activity_id"],
            node_id=row["node_id"],
            status=row["status"],
            scheduled_at=row["scheduled_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            external_id=row["external_id"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
        )
