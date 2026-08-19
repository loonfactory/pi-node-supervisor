"""Atomic draft and revision persistence."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ApplyError, PersistenceError
from .models import DerivedPlan, PowerManagerConfiguration, SupervisorConfiguration


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    exists: bool
    content: bytes | None


def _json_text(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def _snapshot(path: Path) -> _FileSnapshot:
    if not path.exists():
        return _FileSnapshot(False, None)
    return _FileSnapshot(True, path.read_bytes())


def _restore(path: Path, snapshot: _FileSnapshot) -> None:
    if snapshot.exists:
        assert snapshot.content is not None
        _atomic_write(path, snapshot.content)
    else:
        path.unlink(missing_ok=True)


class RevisionStore:
    def __init__(self, state_directory: Path) -> None:
        self.state_directory = state_directory
        self.revisions_directory = state_directory / "revisions"
        self.draft_path = state_directory / "draft.json"
        self.database_path = state_directory / "state.db"
        state_directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.revisions_directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS revisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        source_hash TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('pending', 'active', 'superseded', 'failed')),
                        config_json TEXT NOT NULL,
                        plan_json TEXT NOT NULL,
                        power_json TEXT NOT NULL,
                        error TEXT
                    );
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot initialize revision database: {exc}") from exc

    def save_draft(self, configuration: SupervisorConfiguration) -> None:
        try:
            _atomic_write(self.draft_path, _json_text(configuration).encode("utf-8"))
        except OSError as exc:
            raise PersistenceError(f"cannot save draft: {exc}") from exc

    def load_draft(self) -> SupervisorConfiguration:
        try:
            raw = json.loads(self.draft_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PersistenceError("no draft configuration has been saved") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"cannot read draft: {exc}") from exc
        return SupervisorConfiguration.model_validate(raw)

    def write_power_configuration(
        self, path: Path, configuration: PowerManagerConfiguration
    ) -> None:
        try:
            _atomic_write(path, _json_text(configuration).encode("utf-8"))
        except OSError as exc:
            raise PersistenceError(f"cannot refresh power configuration: {exc}") from exc

    def activate(
        self,
        configuration: SupervisorConfiguration,
        plan: DerivedPlan,
        power_configuration: PowerManagerConfiguration,
        *,
        active_configuration_path: Path,
        power_manager_configuration_path: Path,
    ) -> dict[str, Any]:
        config_json = _json_text(configuration)
        plan_json = _json_text(plan)
        power_json = _json_text(power_configuration)
        created_at = datetime.now(UTC).isoformat()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO revisions
                        (created_at, source_hash, status, config_json, plan_json, power_json)
                    VALUES (?, ?, 'pending', ?, ?, ?)
                    """,
                    (created_at, plan.source_hash, config_json, plan_json, power_json),
                )
                revision_id = int(cursor.lastrowid)
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot create candidate revision: {exc}") from exc

        revision_directory = self.revisions_directory / f"{revision_id:08d}"
        active_snapshot: _FileSnapshot | None = None
        power_snapshot: _FileSnapshot | None = None
        try:
            revision_directory.mkdir(mode=0o750)
            _atomic_write(revision_directory / "config.json", config_json.encode("utf-8"))
            _atomic_write(revision_directory / "plan.json", plan_json.encode("utf-8"))
            _atomic_write(
                revision_directory / "pi-power-manager.json", power_json.encode("utf-8")
            )

            active_snapshot = _snapshot(active_configuration_path)
            power_snapshot = _snapshot(power_manager_configuration_path)
            _atomic_write(power_manager_configuration_path, power_json.encode("utf-8"))
            _atomic_write(active_configuration_path, config_json.encode("utf-8"))

            with self._connect() as connection:
                connection.execute(
                    "UPDATE revisions SET status = 'superseded' WHERE status = 'active'"
                )
                connection.execute(
                    "UPDATE revisions SET status = 'active' WHERE id = ?",
                    (revision_id,),
                )
                connection.execute(
                    """
                    INSERT INTO metadata(key, value) VALUES ('active_revision', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(revision_id),),
                )
        except BaseException as exc:
            rollback_errors: list[str] = []
            for path, snapshot in (
                (active_configuration_path, active_snapshot),
                (power_manager_configuration_path, power_snapshot),
            ):
                if snapshot is None:
                    continue
                try:
                    _restore(path, snapshot)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{path}: {rollback_exc}")
            detail = str(exc)
            if rollback_errors:
                detail += "; rollback errors: " + "; ".join(rollback_errors)
            try:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE revisions SET status = 'failed', error = ? WHERE id = ?",
                        (detail, revision_id),
                    )
            except sqlite3.Error:
                pass
            raise ApplyError(f"revision {revision_id} was not activated: {detail}") from exc

        return self.get_revision(revision_id)

    def get_revision(self, revision_id: int) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, created_at, source_hash, status, error
                    FROM revisions WHERE id = ?
                    """,
                    (revision_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read revision: {exc}") from exc
        if row is None:
            raise PersistenceError(f"revision does not exist: {revision_id}")
        return dict(row)

    def list_revisions(self) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, created_at, source_hash, status, error
                    FROM revisions ORDER BY id DESC
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot list revisions: {exc}") from exc
        return [dict(row) for row in rows]

    def status(self) -> dict[str, Any]:
        revisions = self.list_revisions()
        active = next((item for item in revisions if item["status"] == "active"), None)
        return {
            "active_revision": active,
            "draft_exists": self.draft_path.is_file(),
            "revision_count": len(revisions),
        }

    def active_bundle(
        self,
    ) -> tuple[int, SupervisorConfiguration, DerivedPlan] | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, config_json, plan_json
                    FROM revisions WHERE status = 'active'
                    ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read active revision: {exc}") from exc
        if row is None:
            return None
        try:
            configuration = SupervisorConfiguration.model_validate_json(
                row["config_json"]
            )
            plan = DerivedPlan.model_validate_json(row["plan_json"])
        except (ValueError, TypeError) as exc:
            raise PersistenceError(f"active revision is corrupt: {exc}") from exc
        return int(row["id"]), configuration, plan
