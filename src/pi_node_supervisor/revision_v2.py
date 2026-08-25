"""Atomic v2 draft and revision persistence."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config_v2 import Resolution, resolve_configuration
from .errors import ApplyError, PersistenceError


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    content: bytes | None


class RevisionStoreV2:
    def __init__(self, state_directory: Path) -> None:
        self.state_directory = state_directory
        self.database_path = state_directory / "state.db"
        self.revisions_directory = state_directory / "revisions"
        self.draft_path = state_directory / "draft.json"
        state_directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._discard_legacy_database()
        self.revisions_directory.mkdir(parents=True, exist_ok=True, mode=0o750)
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
        with sqlite3.connect(self.database_path) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            has_tables = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone() is not None
        if has_tables and version != 2:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)
            shutil.rmtree(self.revisions_directory, ignore_errors=True)
            self.draft_path.unlink(missing_ok=True)

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
                        source_json TEXT NOT NULL,
                        resolved_json TEXT NOT NULL,
                        generated_json TEXT NOT NULL,
                        power_json TEXT NOT NULL,
                        error TEXT
                    );
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
                connection.execute("PRAGMA user_version = 2")
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot initialize v2 revision database: {exc}") from exc

    def save_draft(self, document: dict[str, Any]) -> Resolution:
        resolution = resolve_configuration(document)
        _atomic_write(self.draft_path, _json_bytes(_source_document(resolution)))
        return resolution

    def load_draft(self) -> dict[str, Any]:
        try:
            value = json.loads(self.draft_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PersistenceError("no v2 draft configuration has been saved") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"cannot read v2 draft: {exc}") from exc
        if not isinstance(value, dict):
            raise PersistenceError("v2 draft root is not an object")
        resolve_configuration(value)
        return value

    def activate(
        self,
        resolution: Resolution,
        power_document: dict[str, Any],
        *,
        active_configuration_path: Path,
        generated_capture_directory: Path,
        power_configuration_path: Path,
    ) -> dict[str, Any]:
        source_document = _source_document(resolution)
        resolved_document = resolution.resolved.model_dump(mode="json")
        generated_document = resolution.capture_node_configs
        created_at = datetime.now(UTC).isoformat()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO revisions
                        (created_at, source_hash, status, source_json, resolved_json,
                         generated_json, power_json)
                    VALUES (?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        created_at,
                        resolution.resolved.source_hash,
                        _json_text(source_document),
                        _json_text(resolved_document),
                        _json_text(generated_document),
                        _json_text(power_document),
                    ),
                )
                revision_id = int(cursor.lastrowid)
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot create v2 revision: {exc}") from exc

        revision_directory = self.revisions_directory / f"{revision_id:08d}"
        targets: dict[Path, bytes] = {
            revision_directory / "source.json": _json_bytes(source_document),
            revision_directory / "resolved.json": _json_bytes(resolved_document),
            revision_directory / "pi-power-manager.json": _json_bytes(power_document),
            active_configuration_path: _json_bytes(source_document),
            power_configuration_path: _json_bytes(power_document),
        }
        for device_id, document in generated_document.items():
            targets[revision_directory / "capture-nodes" / f"{device_id}.json"] = (
                _json_bytes(document)
            )
            targets[generated_capture_directory / f"{device_id}.json"] = _json_bytes(
                document
            )
        obsolete_generated = tuple(
            path
            for path in generated_capture_directory.glob("*.json")
            if path.stem not in generated_document
        )
        snapshots = {
            path: _snapshot(path) for path in (*targets, *obsolete_generated)
        }
        try:
            for path, content in targets.items():
                _atomic_write(path, content)
            for path in obsolete_generated:
                path.unlink()
            with self._connect() as connection:
                connection.execute(
                    "UPDATE revisions SET status='superseded' WHERE status='active'"
                )
                connection.execute(
                    "UPDATE revisions SET status='active' WHERE id=?", (revision_id,)
                )
                connection.execute(
                    """
                    INSERT INTO metadata(key, value) VALUES ('active_revision', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (str(revision_id),),
                )
        except BaseException as exc:
            rollback_errors = []
            for path, snapshot in snapshots.items():
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
                        "UPDATE revisions SET status='failed', error=? WHERE id=?",
                        (detail, revision_id),
                    )
            except sqlite3.Error:
                pass
            raise ApplyError(f"v2 revision {revision_id} was not activated: {detail}") from exc
        return self.get_revision(revision_id)

    def active(self) -> tuple[int, Resolution] | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, source_json FROM revisions
                    WHERE status='active' ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read active v2 revision: {exc}") from exc
        if row is None:
            return None
        return int(row["id"]), resolve_configuration(json.loads(row["source_json"]))

    def get_revision(self, revision_id: int) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, created_at, source_hash, status, error
                    FROM revisions WHERE id=?
                    """,
                    (revision_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"cannot read v2 revision: {exc}") from exc
        if row is None:
            raise PersistenceError(f"v2 revision does not exist: {revision_id}")
        return dict(row)

    def list_revisions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, source_hash, status, error
                FROM revisions ORDER BY id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def rollback_activation(
        self,
        previous_revision_id: int | None,
        failed_revision_id: int,
        *,
        active_configuration_path: Path,
        generated_capture_directory: Path,
        power_configuration_path: Path,
        error: str,
    ) -> None:
        """Restore the last runtime-startable revision after reload failure."""
        generated_capture_directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        if previous_revision_id is None:
            active_configuration_path.unlink(missing_ok=True)
            power_configuration_path.unlink(missing_ok=True)
            for path in generated_capture_directory.glob("*.json"):
                path.unlink()
        else:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT source_json, generated_json, power_json FROM revisions WHERE id=?",
                    (previous_revision_id,),
                ).fetchone()
            if row is None:
                raise PersistenceError("previous revision no longer exists")
            _atomic_write(active_configuration_path, row["source_json"].encode())
            _atomic_write(power_configuration_path, row["power_json"].encode())
            generated = json.loads(row["generated_json"])
            for path in generated_capture_directory.glob("*.json"):
                if path.stem not in generated:
                    path.unlink()
            for device_id, document in generated.items():
                _atomic_write(
                    generated_capture_directory / f"{device_id}.json",
                    _json_bytes(document),
                )
        with self._connect() as connection:
            connection.execute(
                "UPDATE revisions SET status='failed', error=? WHERE id=?",
                (error, failed_revision_id),
            )
            if previous_revision_id is None:
                connection.execute("DELETE FROM metadata WHERE key='active_revision'")
            else:
                connection.execute(
                    "UPDATE revisions SET status='active' WHERE id=?",
                    (previous_revision_id,),
                )
                connection.execute(
                    """INSERT INTO metadata(key, value) VALUES ('active_revision', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (str(previous_revision_id),),
                )


def _source_document(resolution: Resolution) -> dict[str, Any]:
    return resolution.source.model_dump(mode="json", by_alias=True)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _json_bytes(value: Any) -> bytes:
    return _json_text(value).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot(path: Path) -> FileSnapshot:
    try:
        return FileSnapshot(True, path.read_bytes())
    except FileNotFoundError:
        return FileSnapshot(False, None)


def _restore(path: Path, snapshot: FileSnapshot) -> None:
    if snapshot.exists:
        assert snapshot.content is not None
        _atomic_write(path, snapshot.content)
    else:
        path.unlink(missing_ok=True)
