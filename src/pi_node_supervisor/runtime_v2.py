"""Schema-v2 runtime state without retry or pending-delivery queues."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .errors import PersistenceError


RunStatus = Literal["running", "succeeded", "failed", "missed", "interrupted"]


class RunRecordV2(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    revision_id: int
    device_id: str
    device_kind: Literal["camera", "sensor"]
    trigger: Literal["scheduled", "manual"]
    scheduled_at: datetime
    status: RunStatus
    started_at: datetime | None
    completed_at: datetime | None
    capture_id: str | None
    result: dict[str, Any] | None
    error: str | None


class EventRecordV2(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    occurred_at: datetime
    event_type: str
    severity: Literal["info", "warning", "error"]
    run_id: str | None
    device_id: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    name: str
    media_type: str
    size: int
    sha256: str
    captured_at: datetime


class ActiveWork:
    def __init__(self) -> None:
        self._count = 0
        self._draining = False
        self._condition = threading.Condition()

    @property
    def count(self) -> int:
        with self._condition:
            return self._count

    def acquire(self) -> None:
        with self._condition:
            if self._draining:
                raise RuntimeError("shutdown is being prepared; new work is not accepted")
            self._count += 1

    def release(self) -> None:
        with self._condition:
            if self._count == 0:
                raise RuntimeError("active work counter is already zero")
            self._count -= 1
            self._condition.notify_all()

    def begin_shutdown_if_idle(self) -> bool:
        with self._condition:
            if self._count:
                return False
            self._draining = True
            return True

    def cancel_shutdown(self) -> None:
        with self._condition:
            self._draining = False
            self._condition.notify_all()


class RuntimeStoreV2:
    SCHEMA_VERSION = 2

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._discard_legacy_database()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _discard_legacy_database(self) -> None:
        if not self.database_path.exists():
            return
        try:
            connection = sqlite3.connect(self.database_path)
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                has_tables = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
                ).fetchone() is not None
            finally:
                connection.close()
        except sqlite3.Error:
            version, has_tables = 0, True
        if has_tables and version != self.SCHEMA_VERSION:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        revision_id INTEGER NOT NULL,
                        device_id TEXT NOT NULL,
                        device_kind TEXT NOT NULL CHECK(device_kind IN ('camera', 'sensor')),
                        trigger TEXT NOT NULL CHECK(trigger IN ('scheduled', 'manual')),
                        scheduled_at TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed', 'missed', 'interrupted')),
                        started_at TEXT,
                        completed_at TEXT,
                        capture_id TEXT,
                        result_json TEXT,
                        error TEXT,
                        UNIQUE(revision_id, device_id, scheduled_at)
                    );
                    CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
                    CREATE INDEX IF NOT EXISTS idx_runs_scheduled ON runs(scheduled_at DESC);

                    CREATE TABLE IF NOT EXISTS artifacts (
                        artifact_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES runs(run_id),
                        name TEXT NOT NULL,
                        media_type TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        captured_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);

                    CREATE TABLE IF NOT EXISTS storage_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL REFERENCES runs(run_id),
                        artifact_id TEXT REFERENCES artifacts(artifact_id),
                        target TEXT NOT NULL,
                        path TEXT NOT NULL,
                        succeeded INTEGER NOT NULL,
                        size INTEGER,
                        error TEXT,
                        completed_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_storage_results_run ON storage_results(run_id);

                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        severity TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'error')),
                        run_id TEXT,
                        device_id TEXT,
                        payload_json TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS acknowledged_failed_runs (
                        run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                        acknowledged_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS temporary_power_windows (
                        id TEXT PRIMARY KEY,
                        start_at TEXT NOT NULL,
                        end_at TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot initialize v2 runtime database: {exc}") from exc

    def claim_run(
        self,
        *,
        revision_id: int,
        device_id: str,
        device_kind: Literal["camera", "sensor"],
        scheduled_at: datetime,
        trigger: Literal["scheduled", "manual"] = "scheduled",
        status: RunStatus = "running",
        error: str | None = None,
    ) -> RunRecordV2 | None:
        scheduled = _aware_utc(scheduled_at)
        now = datetime.now(UTC)
        run_id = uuid.uuid4().hex
        started_at = now if status == "running" else None
        completed_at = None if status == "running" else now
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO runs
                        (run_id, revision_id, device_id, device_kind, trigger,
                         scheduled_at, status, started_at, completed_at, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        revision_id,
                        device_id,
                        device_kind,
                        trigger,
                        scheduled.isoformat(),
                        status,
                        started_at.isoformat() if started_at else None,
                        completed_at.isoformat() if completed_at else None,
                        error,
                    ),
                )
                if cursor.rowcount == 0:
                    return None
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot claim v2 run: {exc}") from exc
        return self.get_run(run_id)

    def complete_run(
        self,
        run_id: str,
        *,
        status: Literal["succeeded", "failed", "interrupted"],
        capture_id: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> RunRecordV2:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE runs SET status=?, completed_at=?, capture_id=?, result_json=?, error=?
                    WHERE run_id=? AND status='running'
                    """,
                    (
                        status,
                        datetime.now(UTC).isoformat(),
                        capture_id,
                        json.dumps(result, ensure_ascii=False, sort_keys=True) if result else None,
                        error,
                        run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PersistenceError(f"run is not active: {run_id}")
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot complete v2 run: {exc}") from exc
        return self.get_run(run_id)

    def recover_interrupted(self) -> tuple[RunRecordV2, ...]:
        now = datetime.now(UTC).isoformat()
        reason = "supervisor restarted while work was active; automatic replay is disabled"
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT run_id, device_id FROM runs WHERE status='running'"
                ).fetchall()
                for row in rows:
                    connection.execute(
                        "UPDATE runs SET status='interrupted', completed_at=?, error=? WHERE run_id=?",
                        (now, reason, row["run_id"]),
                    )
                    self._insert_event(
                        connection,
                        "run.interrupted",
                        "warning",
                        row["run_id"],
                        row["device_id"],
                        {"reason": reason, "replayed": False},
                    )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot recover v2 runs: {exc}") from exc
        return tuple(self.get_run(row["run_id"]) for row in rows)

    def add_artifact(
        self,
        *,
        run_id: str,
        name: str,
        media_type: str,
        size: int,
        sha256: str,
        captured_at: datetime,
    ) -> ArtifactRecord:
        artifact = ArtifactRecord(
            artifact_id=uuid.uuid4().hex,
            run_id=run_id,
            name=name,
            media_type=media_type,
            size=size,
            sha256=sha256,
            captured_at=_aware_utc(captured_at),
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        artifact.artifact_id,
                        artifact.run_id,
                        artifact.name,
                        artifact.media_type,
                        artifact.size,
                        artifact.sha256,
                        artifact.captured_at.isoformat(),
                    ),
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot record artifact: {exc}") from exc
        return artifact

    def add_storage_result(
        self,
        *,
        run_id: str,
        artifact_id: str | None,
        target: str,
        path: str,
        succeeded: bool,
        size: int | None,
        error: str | None,
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO storage_results
                        (run_id, artifact_id, target, path, succeeded, size, error, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        artifact_id,
                        target,
                        path,
                        int(succeeded),
                        size,
                        error,
                        datetime.now(UTC).isoformat(),
                    ),
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot record Storage result: {exc}") from exc

    def append_event(
        self,
        event_type: str,
        *,
        severity: Literal["info", "warning", "error"] = "info",
        run_id: str | None = None,
        device_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> EventRecordV2:
        try:
            with self._connect() as connection:
                event_id = self._insert_event(
                    connection,
                    event_type,
                    severity,
                    run_id,
                    device_id,
                    payload or {},
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot append v2 event: {exc}") from exc
        return EventRecordV2(
            id=event_id,
            occurred_at=datetime.now(UTC),
            event_type=event_type,
            severity=severity,
            run_id=run_id,
            device_id=device_id,
            payload=payload or {},
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        event_type: str,
        severity: str,
        run_id: str | None,
        device_id: str | None,
        payload: dict[str, Any],
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO events(occurred_at, event_type, severity, run_id, device_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(),
                event_type,
                severity,
                run_id,
                device_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        return int(cursor.lastrowid)

    def get_run(self, run_id: str) -> RunRecordV2:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read v2 run: {exc}") from exc
        if row is None:
            raise PersistenceError(f"run does not exist: {run_id}")
        return _run(row)

    def list_runs(self, limit: int = 100, offset: int = 0) -> tuple[RunRecordV2, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM runs ORDER BY scheduled_at DESC, run_id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot list v2 runs: {exc}") from exc
        return tuple(_run(row) for row in rows)

    def list_storage_results(self, run_id: str) -> tuple[dict[str, Any], ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT target, path, succeeded, size, error, completed_at
                    FROM storage_results WHERE run_id=? ORDER BY id
                    """,
                    (run_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot list Storage results: {exc}") from exc
        return tuple(
            {
                "target": row["target"],
                "path": row["path"],
                "succeeded": bool(row["succeeded"]),
                "size": row["size"],
                "error": row["error"],
                "completed_at": row["completed_at"],
            }
            for row in rows
        )

    def list_events(self, limit: int = 100, offset: int = 0) -> tuple[EventRecordV2, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM events ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot list v2 events: {exc}") from exc
        return tuple(
            EventRecordV2(
                id=row["id"],
                occurred_at=row["occurred_at"],
                event_type=row["event_type"],
                severity=row["severity"],
                run_id=row["run_id"],
                device_id=row["device_id"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        )

    def list_data(self, limit: int = 100, offset: int = 0) -> tuple[dict[str, Any], ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT a.*, r.device_id, r.capture_id
                    FROM artifacts a JOIN runs r ON r.run_id=a.run_id
                    ORDER BY a.captured_at DESC, a.artifact_id DESC LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot list v2 data: {exc}") from exc
        return tuple(dict(row) for row in rows)

    def counts(self) -> dict[str, int]:
        try:
            with self._connect() as connection:
                failed_statuses = "('failed','interrupted','missed')"
                return {
                    "runs": int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
                    "running": int(
                        connection.execute(
                            "SELECT COUNT(*) FROM runs WHERE status='running'"
                        ).fetchone()[0]
                    ),
                    "failedRuns": int(
                        connection.execute(
                            f"""
                            SELECT COUNT(*) FROM runs r
                            WHERE r.status IN {failed_statuses}
                              AND NOT EXISTS (
                                  SELECT 1 FROM acknowledged_failed_runs a
                                  WHERE a.run_id=r.run_id
                              )
                            """
                        ).fetchone()[0]
                    ),
                    "totalFailedRuns": int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM runs WHERE status IN {failed_statuses}"
                        ).fetchone()[0]
                    ),
                    "artifacts": int(
                        connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
                    ),
                    "storageFailures": int(
                        connection.execute(
                            "SELECT COUNT(*) FROM storage_results WHERE succeeded=0"
                        ).fetchone()[0]
                    ),
                    "events": int(
                        connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                    ),
                }
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot count v2 runtime records: {exc}") from exc

    def acknowledge_failed_runs(self) -> int:
        """Mark current failed runs as seen without removing their history."""
        try:
            with self._connect() as connection:
                unacknowledged = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM runs r
                        WHERE r.status IN ('failed','interrupted','missed')
                          AND NOT EXISTS (
                              SELECT 1 FROM acknowledged_failed_runs a
                              WHERE a.run_id=r.run_id
                          )
                        """
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO acknowledged_failed_runs(run_id, acknowledged_at)
                    SELECT run_id, ? FROM runs
                    WHERE status IN ('failed','interrupted','missed')
                    """,
                    (datetime.now(UTC).isoformat(),),
                )
                return unacknowledged
        except sqlite3.Error as exc:
            raise PersistenceError(
                f"cannot acknowledge failed runtime records: {exc}"
            ) from exc


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("runtime timestamps must include a timezone")
    return value.astimezone(UTC)


def _run(row: sqlite3.Row) -> RunRecordV2:
    return RunRecordV2(
        run_id=row["run_id"],
        revision_id=row["revision_id"],
        device_id=row["device_id"],
        device_kind=row["device_kind"],
        trigger=row["trigger"],
        scheduled_at=row["scheduled_at"],
        status=row["status"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        capture_id=row["capture_id"],
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        error=row["error"],
    )
