from __future__ import annotations

from pathlib import Path, PurePosixPath

from pi_node_supervisor.config_v2 import ResolvedStorage
from pi_node_supervisor.storage_v2 import LocalStorageTarget, SaveResult, StoragePipeline


def local_storage(root: Path, name="local") -> ResolvedStorage:
    return ResolvedStorage(
        name=name,
        type="local",
        base_path=str(root.parent),
        path=root.name,
        final_path=str(root),
        settings={"type": "local", "basePath": str(root.parent)},
    )


def test_pipeline_saves_file_atomically_to_each_target(tmp_path: Path) -> None:
    source = tmp_path / "source.dng"
    source.write_bytes(b"capture")
    pipeline = StoragePipeline(
        (
            LocalStorageTarget(local_storage(tmp_path / "first", "first")),
            LocalStorageTarget(local_storage(tmp_path / "second", "second")),
        )
    )

    results = pipeline.save_file(source, PurePosixPath("2030/01/01/capture.dng"))

    assert all(item.succeeded for item in results)
    assert (tmp_path / "first/2030/01/01/capture.dng").read_bytes() == b"capture"
    assert (tmp_path / "second/2030/01/01/capture.dng").read_bytes() == b"capture"


def test_local_storage_is_kept_below_managed_root(tmp_path: Path) -> None:
    source = tmp_path / "source.dng"
    source.write_bytes(b"capture")
    storage = ResolvedStorage(
        name="local",
        type="local",
        base_path="/",
        path="rgb",
        final_path="/rgb",
        settings={"type": "local", "basePath": "/"},
    )
    pipeline = StoragePipeline.from_resolved(
        (storage,), local_root=tmp_path / "managed"
    )

    result = pipeline.save_file(source, PurePosixPath("capture.dng"))

    assert result[0].succeeded
    assert (tmp_path / "managed/rgb/capture.dng").read_bytes() == b"capture"


def test_failure_does_not_stop_later_storage_target(tmp_path: Path) -> None:
    class BrokenTarget:
        name = "broken"

        def save_atomic(self, _source, _relative):
            raise OSError("network unavailable")

    source = tmp_path / "source.dng"
    source.write_bytes(b"capture")
    pipeline = StoragePipeline(
        (
            BrokenTarget(),
            LocalStorageTarget(local_storage(tmp_path / "working", "working")),
        )
    )

    results = pipeline.save_file(source, PurePosixPath("capture.dng"))

    assert [item.succeeded for item in results] == [False, True]
    assert (tmp_path / "working/capture.dng").is_file()


def test_sensor_row_is_appended_to_each_target_date_file(tmp_path: Path) -> None:
    pipeline = StoragePipeline(
        (LocalStorageTarget(local_storage(tmp_path / "sensor")),)
    )
    path = PurePosixPath("2030/01/01/2030-01-01.csv")

    first = pipeline.append_sensor_row(
        path,
        header=("timestamp", "temperatureC", "humidityPercent"),
        row=("2030-01-01T12:00:00+09:00", 20.1, 60.2),
    )
    second = pipeline.append_sensor_row(
        path,
        header=("timestamp", "temperatureC", "humidityPercent"),
        row=("2030-01-01T12:30:00+09:00", 20.3, 61.0),
    )

    assert first[0].succeeded and second[0].succeeded
    content = (tmp_path / "sensor/2030/01/01/2030-01-01.csv").read_text()
    assert content.splitlines() == [
        "timestamp,temperatureC,humidityPercent",
        "2030-01-01T12:00:00+09:00,20.1,60.2",
        "2030-01-01T12:30:00+09:00,20.3,61.0",
    ]


def test_startup_cleanup_removes_only_incomplete_storage_files(tmp_path: Path) -> None:
    target = LocalStorageTarget(local_storage(tmp_path / "camera"))
    target.root.mkdir()
    incomplete = target.root / ".capture.dng.42.uploading"
    completed = target.root / "capture.dng"
    incomplete.write_bytes(b"partial")
    completed.write_bytes(b"complete")

    result = StoragePipeline((target,)).cleanup_temporary()

    assert result[0].succeeded
    assert not incomplete.exists()
    assert completed.read_bytes() == b"complete"
