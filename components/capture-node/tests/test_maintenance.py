from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from capture_node.core.maintenance import (
    MaintenanceScheduler,
    RetentionOptions,
    StorageMaintenance,
)
from capture_node.core.storages.file_storage import FileStorage
from conftest import FakeStorageProvider


def _stored_capture(root: Path, name: str, modified_at: datetime) -> Path:
    directory = root / name[:8]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"capture")
    timestamp = modified_at.timestamp()
    os.utime(path, (timestamp, timestamp))
    return path


def test_retention_deletes_only_files_older_than_the_policy(tmp_path: Path) -> None:
    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    expired = _stored_capture(
        tmp_path,
        "20260203T000000.000000Z_expired.png",
        now - timedelta(days=181),
    )
    retained = _stored_capture(
        tmp_path,
        "20260204T120000.000000Z_retained.png",
        now - timedelta(days=180),
    )
    storage = FileStorage("local", tmp_path)
    maintenance = StorageMaintenance(
        FakeStorageProvider(storage),
        (RetentionOptions(target="local", maxAgeDays=180),),
    )

    result = maintenance.run(now)

    assert result.deleted == (("local", expired.name),)
    assert result.failures == ()
    assert not expired.exists()
    assert retained.exists()


def test_retention_does_not_delete_from_a_target_without_a_policy(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    expired_at = now - timedelta(days=366)
    local_root = tmp_path / "local"
    archive_root = tmp_path / "archive"
    local = _stored_capture(
        local_root,
        "20250802T000000.000000Z_local.png",
        expired_at,
    )
    archive = _stored_capture(
        archive_root,
        "20250802T000000.000000Z_archive.png",
        expired_at,
    )
    maintenance = StorageMaintenance(
        FakeStorageProvider(
            FileStorage("local", local_root),
            FileStorage("archive", archive_root),
        ),
        (RetentionOptions(target="local", maxAgeDays=365),),
    )

    result = maintenance.run(now)

    assert result.deleted == (("local", local.name),)
    assert result.failures == ()
    assert not local.exists()
    assert archive.exists()


def test_maintenance_scheduler_runs_immediately_and_stops() -> None:
    called = threading.Event()
    scheduler = MaintenanceScheduler(3600, called.set)

    scheduler.start()
    assert called.wait(0.2)
    scheduler.stop()
