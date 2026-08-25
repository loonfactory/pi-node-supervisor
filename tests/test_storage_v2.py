from __future__ import annotations

import ssl
from ftplib import error_perm
from pathlib import Path, PurePosixPath

import pytest

from pi_node_supervisor.config_v2 import ResolvedStorage
from pi_node_supervisor import storage_v2
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


def test_ftps_cleanup_falls_back_to_nlst_when_mlsd_is_unsupported() -> None:
    class LegacyFtps:
        def __init__(self) -> None:
            self.files = {
                "/capture/.partial.1.uploading",
                "/capture/complete.dng",
                "/capture/day/.nested.2.uploading",
            }

        def mlsd(self, _directory):
            raise error_perm("500 Command not understood.")

        def nlst(self, directory):
            prefix = f"{directory.rstrip('/')}/"
            entries = {path for path in self.files if path.startswith(prefix)}
            if directory == "/capture":
                entries.add("/capture/day")
            return [path for path in entries if "/" not in path[len(prefix):]]

        def size(self, path):
            if path not in self.files:
                raise error_perm("550 not a file")
            return 1

        def cwd(self, path):
            if path != "/capture/day":
                raise error_perm("550 not a directory")

        def delete(self, path):
            self.files.remove(path)

    ftp = LegacyFtps()

    removed = storage_v2._ftps_cleanup(ftp, "/capture")

    assert removed == 2
    assert ftp.files == {"/capture/complete.dng"}


def test_ftps_replaces_existing_file_without_server_side_overwrite() -> None:
    class NoOverwriteFtps:
        def __init__(self) -> None:
            self.files = {
                "/capture/.data.csv.42.uploading": b"new",
                "/capture/data.csv": b"old",
            }

        def rename(self, source, destination):
            if destination in self.files:
                raise error_perm("550 Cannot create a file when that file already exists")
            self.files[destination] = self.files.pop(source)

        def delete(self, path):
            del self.files[path]

    ftp = NoOverwriteFtps()

    storage_v2._ftps_replace(
        ftp,
        "/capture/.data.csv.42.uploading",
        "/capture/data.csv",
        "/capture/.data.csv.42.replacing",
    )

    assert ftp.files == {"/capture/data.csv": b"new"}


def test_ftps_cleanup_restores_replacement_interrupted_before_commit() -> None:
    class InterruptedFtps:
        def __init__(self) -> None:
            self.files = {
                "/capture/.data.csv.42.uploading": b"new",
                "/capture/.data.csv.42.replacing": b"old",
            }

        def mlsd(self, directory):
            prefix = f"{directory.rstrip('/')}/"
            for path in list(self.files):
                if path.startswith(prefix) and "/" not in path[len(prefix):]:
                    yield path[len(prefix):], {"type": "file"}

        def size(self, path):
            if path not in self.files:
                raise error_perm("550 missing")
            return len(self.files[path])

        def rename(self, source, destination):
            self.files[destination] = self.files.pop(source)

        def delete(self, path):
            del self.files[path]

    ftp = InterruptedFtps()

    removed = storage_v2._ftps_cleanup(ftp, "/capture")

    assert removed == 2
    assert ftp.files == {"/capture/data.csv": b"old"}


@pytest.mark.parametrize("failure", [TimeoutError(), ssl.SSLEOFError()])
def test_ftps_tolerates_missing_data_channel_close_notify(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    class FakeTlsSocket:
        def __init__(self) -> None:
            self.timeout = 30.0

        def gettimeout(self):
            return self.timeout

        def settimeout(self, timeout):
            self.timeout = timeout

        def unwrap(self):
            raise failure

    monkeypatch.setattr(storage_v2.ssl, "SSLSocket", FakeTlsSocket)
    connection = FakeTlsSocket()

    storage_v2._finish_tls_data_channel(connection)

    assert connection.timeout == 1.0
