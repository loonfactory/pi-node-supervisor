from __future__ import annotations

import csv
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import pytest

from pi_node_supervisor.models import SensorReading, SensorStorageSettings
from pi_node_supervisor.planner import Planner
from pi_node_supervisor.runtime_store import RuntimeStore
from pi_node_supervisor.sensor_adapter import CwtSensorAdapter
from pi_node_supervisor.sensor_storage import (
    CSV_HEADER,
    FileSensorMirror,
    FtpsSensorMirror,
    SensorCsvRepository,
    SensorStorageCoordinator,
    SftpSensorMirror,
)
from conftest import configuration


def reading(observed_at, temperature=20.5, humidity=61.2):
    return SensorReading(
        observed_at=observed_at,
        temperature_c=temperature,
        humidity_percent=humidity,
    )


def test_daily_csv_uses_scheduled_slot_three_columns_and_arbitrary_row_count(tmp_path):
    repository = SensorCsvRepository(tmp_path / "local", "environment0", "Asia/Seoul")
    day = datetime(2030, 1, 1, 15, tzinfo=UTC)
    slots = [day, day + timedelta(minutes=7), day + timedelta(hours=8, seconds=3)]
    for index, slot in enumerate(slots):
        repository.commit(
            slot,
            reading(slot + timedelta(seconds=9), 20 + index, 60 + index),
        )

    path = tmp_path / "local/environment0/2030/01/2030-01-02.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert tuple(rows[0]) == CSV_HEADER
    assert [row["timestamp"] for row in rows] == [
        "2030-01-02T00:00:00+09:00",
        "2030-01-02T00:07:00+09:00",
        "2030-01-02T08:00:03+09:00",
    ]
    assert len(rows) == 3
    assert set(rows[0]) == {"timestamp", "temperature_c", "humidity_percent"}


def test_duplicate_slot_is_idempotent_and_preserves_first_authoritative_row(tmp_path):
    repository = SensorCsvRepository(tmp_path, "environment0", "UTC")
    slot = datetime(2030, 1, 1, tzinfo=UTC)

    repository.commit(slot, reading(slot, 20, 60))
    repository.commit(slot, reading(slot + timedelta(seconds=5), 99, 99))

    path = tmp_path / "environment0/2030/01/2030-01-01.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows == [
        {
            "timestamp": "2030-01-01T00:00:00+00:00",
            "temperature_c": "20",
            "humidity_percent": "60",
        }
    ]


def test_timezone_date_boundary_creates_distinct_daily_files(tmp_path):
    repository = SensorCsvRepository(tmp_path, "environment0", "Asia/Seoul")
    before = datetime(2030, 1, 1, 14, 59, tzinfo=UTC)
    after = datetime(2030, 1, 1, 15, 0, tzinfo=UTC)

    first, _, _ = repository.commit(before, reading(before))
    second, _, _ = repository.commit(after, reading(after))

    assert first.name == "2030-01-01.csv"
    assert second.name == "2030-01-02.csv"


def test_failed_atomic_replace_leaves_previous_complete_csv(tmp_path, monkeypatch):
    repository = SensorCsvRepository(tmp_path, "environment0", "UTC")
    first_slot = datetime(2030, 1, 1, tzinfo=UTC)
    path, _, _ = repository.commit(first_slot, reading(first_slot, 20, 60))
    original = path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("pi_node_supervisor.sensor_storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        repository.commit(
            first_slot + timedelta(minutes=1),
            reading(first_slot + timedelta(minutes=1), 21, 61),
        )

    assert path.read_bytes() == original
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_file_mirror_replaces_full_local_csv(tmp_path):
    source = tmp_path / "source.csv"
    source.write_bytes(b"timestamp,temperature_c,humidity_percent\r\n")
    settings = SensorStorageSettings.model_validate(
        {
            "local_directory": tmp_path / "local",
            "mirrors": [
                {"type": "file", "name": "backup", "directory": tmp_path / "mirror"}
            ],
        }
    )
    mirror = FileSensorMirror(settings.mirrors[0])
    relative = PurePosixPath("environment0/2030/01/2030-01-01.csv")

    mirror.upload_atomic(source, relative)
    source.write_bytes(source.read_bytes() + b"2030-01-01T00:00:00Z,20,60\r\n")
    mirror.upload_atomic(source, relative)

    assert (
        tmp_path / "mirror" / Path(*relative.parts)
    ).read_bytes() == source.read_bytes()
    assert not list((tmp_path / "mirror" / "environment0/2030/01").glob(".*"))


def test_remote_failure_keeps_local_csv_and_restart_retry_has_no_duplicate_rows(tmp_path):
    runtime = RuntimeStore(tmp_path / "state.db")
    settings = SensorStorageSettings.model_validate(
        {
            "local_directory": tmp_path / "local",
            "mirrors": [
                {"type": "file", "name": "remote", "directory": tmp_path / "unused"}
            ],
            "retry_base_seconds": 1,
            "retry_max_seconds": 4,
        }
    )

    class Mirror:
        name = "remote"

        def __init__(self):
            self.fail = True
            self.uploads = []

        def upload_atomic(self, local_path, relative_path):
            if self.fail:
                raise OSError("remote offline")
            self.uploads.append((str(relative_path), local_path.read_bytes()))

    mirror = Mirror()
    now = datetime(2030, 1, 1, tzinfo=UTC)
    coordinator = SensorStorageCoordinator(
        node_id="environment0",
        timezone="UTC",
        settings=settings,
        runtime=runtime,
        mirrors={"remote": mirror},
        clock=lambda: now,
    )

    result = coordinator.commit(now, reading(now))
    assert result["delivery"] == {"succeeded": 0, "failed": 1, "backlog": 1}
    assert Path(result["csv_path"]).is_file()
    assert runtime.sensor_delivery_status()["counts"]["failed"] == 1

    mirror.fail = False
    restarted = SensorStorageCoordinator(
        node_id="environment0",
        timezone="UTC",
        settings=settings,
        runtime=RuntimeStore(tmp_path / "state.db"),
        mirrors={"remote": mirror},
        clock=lambda: now + timedelta(seconds=1),
    )
    recovered = restarted.sync_pending()

    assert recovered == {"succeeded": 1, "failed": 0, "backlog": 0}
    assert len(mirror.uploads) == 1
    with Path(result["csv_path"]).open(encoding="utf-8", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 1
    event_types = {item.event_type for item in runtime.list_events()}
    assert {"sensor.csv_committed", "sensor.mirror_failed", "sensor.mirror_succeeded"} <= event_types


@pytest.mark.parametrize("target", ["file", "sftp", "ftps"])
def test_transport_failure_is_logged_visible_and_retryable_without_duplicate_rows(
    tmp_path, caplog, target
):
    runtime = RuntimeStore(tmp_path / "state.db")
    mirror_settings = {
        "file": {"type": "file", "name": "file", "directory": tmp_path / "unused"},
        "sftp": {
            "type": "sftp", "name": "sftp", "host": "example.test",
            "username": "sensor", "remote_directory": "/archive",
            "password_env": "SFTP_PASSWORD",
        },
        "ftps": {
            "type": "ftps", "name": "ftps", "host": "example.test",
            "username": "sensor", "remote_directory": "/archive",
            "password_env": "FTPS_PASSWORD",
        },
    }
    settings = SensorStorageSettings.model_validate(
        {
            "local_directory": tmp_path / "local",
            "mirrors": [mirror_settings[target]],
            "retry_base_seconds": 1,
            "retry_max_seconds": 1,
        }
    )

    class FailingTransport:
        name = target

        def __init__(self):
            self.fail = True
            self.uploads = []

        def upload_atomic(self, local_path, relative_path):
            if self.fail:
                raise OSError(f"{target} rename failed after temporary upload")
            self.uploads.append((str(relative_path), local_path.read_bytes()))

    transport = FailingTransport()
    slot = datetime(2030, 1, 1, tzinfo=UTC)
    coordinator = SensorStorageCoordinator(
        node_id="environment0",
        timezone="UTC",
        settings=settings,
        runtime=runtime,
        mirrors={target: transport},
        clock=lambda: slot,
    )

    with caplog.at_level(logging.WARNING, logger="pi_node_supervisor.sensor_storage"):
        result = coordinator.commit(slot, reading(slot))

    error = f"OSError: {target} rename failed after temporary upload"
    status = runtime.sensor_delivery_status()
    failed_events = [
        event for event in runtime.list_events()
        if event.event_type == "sensor.mirror_failed"
    ]
    assert result["delivery"] == {"succeeded": 0, "failed": 1, "backlog": 1}
    assert status["counts"]["failed"] == 1
    assert status["items"][0]["last_error"] == error
    assert failed_events[0].payload["error"] == error
    assert error in caplog.text

    transport.fail = False
    coordinator.clock = lambda: slot + timedelta(seconds=1)
    assert coordinator.sync_pending() == {"succeeded": 1, "failed": 0, "backlog": 0}
    assert len(transport.uploads) == 1
    with Path(result["csv_path"]).open(encoding="utf-8", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 1


def test_transport_error_redacts_configured_secret(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("FTPS_PASSWORD", "do-not-log-this")
    runtime = RuntimeStore(tmp_path / "state.db")
    settings = SensorStorageSettings.model_validate(
        {
            "local_directory": tmp_path / "local",
            "mirrors": [{
                "type": "ftps",
                "name": "ftps",
                "host": "example.test",
                "username": "sensor",
                "remote_directory": "/archive",
                "password_env": "FTPS_PASSWORD",
            }],
        }
    )

    class Mirror:
        name = "ftps"

        def upload_atomic(self, _local_path, _relative_path):
            raise OSError("server rejected do-not-log-this")

    slot = datetime(2030, 1, 1, tzinfo=UTC)
    coordinator = SensorStorageCoordinator(
        node_id="environment0",
        timezone="UTC",
        settings=settings,
        runtime=runtime,
        mirrors={"ftps": Mirror()},
        clock=lambda: slot,
    )

    with caplog.at_level(logging.WARNING, logger="pi_node_supervisor.sensor_storage"):
        coordinator.commit(slot, reading(slot))

    last_error = runtime.sensor_delivery_status()["items"][0]["last_error"]
    assert last_error == "OSError: server rejected ***"
    assert "do-not-log-this" not in caplog.text


def test_sftp_uses_temporary_upload_then_atomic_posix_rename(tmp_path, monkeypatch):
    source = tmp_path / "source.csv"
    source.write_bytes(b"csv")

    class Channel:
        timeout = None

        def settimeout(self, value):
            self.timeout = value

    class Sftp:
        def __init__(self):
            self.channel = Channel()
            self.puts = []
            self.renames = []

        def get_channel(self): return self.channel
        def mkdir(self, _path): pass
        def put(self, source_path, target_path, confirm):
            self.puts.append((source_path, target_path, confirm))
        def posix_rename(self, source_path, target_path):
            self.renames.append((source_path, target_path))
        def close(self): pass

    sftp = Sftp()

    class Client:
        def load_host_keys(self, _path): pass
        def set_missing_host_key_policy(self, _policy): pass
        def connect(self, *args, **kwargs): self.connection = (args, kwargs)
        def open_sftp(self): return sftp
        def close(self): pass

    monkeypatch.setitem(
        sys.modules,
        "paramiko",
        type("Paramiko", (), {"SSHClient": Client, "RejectPolicy": object}),
    )
    monkeypatch.setenv("SFTP_PASSWORD", "secret")
    settings = SensorStorageSettings.model_validate(
        {
            "local_directory": tmp_path,
            "mirrors": [{
                "type": "sftp", "name": "sftp", "host": "example.test",
                "username": "sensor", "remote_directory": "/archive",
                "password_env": "SFTP_PASSWORD",
            }],
        }
    ).mirrors[0]

    SftpSensorMirror(settings).upload_atomic(
        source, PurePosixPath("environment0/2030/01/2030-01-01.csv")
    )

    assert sftp.puts[0][1] == "/archive/environment0/2030/01/.2030-01-01.csv.uploading"
    assert sftp.renames == [(
        "/archive/environment0/2030/01/.2030-01-01.csv.uploading",
        "/archive/environment0/2030/01/2030-01-01.csv",
    )]


def test_ftps_uses_temporary_upload_then_rename(tmp_path, monkeypatch):
    source = tmp_path / "source.csv"
    source.write_bytes(b"csv")

    class Socket:
        def settimeout(self, value): self.timeout = value

    class Ftp:
        instance = None

        def __init__(self, **_kwargs):
            Ftp.instance = self
            self.sock = Socket()
            self.stored = []
            self.renames = []

        def connect(self, *_args): pass
        def login(self, *_args): pass
        def prot_p(self): pass
        def mkd(self, _path): pass
        def storbinary(self, command, stream): self.stored.append((command, stream.read()))
        def rename(self, source_path, target_path): self.renames.append((source_path, target_path))
        def quit(self): pass
        def close(self): pass

    monkeypatch.setattr("pi_node_supervisor.sensor_storage.ftplib.FTP_TLS", Ftp)
    monkeypatch.setenv("FTPS_PASSWORD", "secret")
    settings = SensorStorageSettings.model_validate(
        {
            "local_directory": tmp_path,
            "mirrors": [{
                "type": "ftps", "name": "ftps", "host": "example.test",
                "username": "sensor", "remote_directory": "/archive",
                "password_env": "FTPS_PASSWORD",
            }],
        }
    ).mirrors[0]

    FtpsSensorMirror(settings).upload_atomic(
        source, PurePosixPath("environment0/2030/01/2030-01-01.csv")
    )

    assert Ftp.instance.stored == [(
        "STOR /archive/environment0/2030/01/.2030-01-01.csv.uploading", b"csv"
    )]
    assert Ftp.instance.renames == [(
        "/archive/environment0/2030/01/.2030-01-01.csv.uploading",
        "/archive/environment0/2030/01/2030-01-01.csv",
    )]


def test_storage_enabled_adapter_keeps_values_out_of_sqlite_facing_result(tmp_path, registry):
    slot = datetime(2030, 1, 1, tzinfo=UTC)
    activity = Planner(registry).plan(
        configuration(
            {
                "id": "soil",
                "type": "sensor-collection",
                "at": slot.isoformat(),
                "node": "environment0",
                "profile": "temperature-humidity",
            },
            timezone="UTC",
        )
    ).activities[0]
    settings = SensorStorageSettings(local_directory=tmp_path / "local")
    storage = SensorStorageCoordinator(
        node_id="environment0",
        timezone="UTC",
        settings=settings,
        runtime=RuntimeStore(tmp_path / "state.db"),
    )

    class Provider:
        def read(self): return reading(slot + timedelta(seconds=2), 22.1, 58.4)
        def close(self): pass

    result = CwtSensorAdapter(
        registry.require("environment0"), Provider(), storage
    ).execute(activity, "run-1")

    assert "temperature_c" not in result
    assert "humidity_percent" not in result
    assert result["measurement_stored"] is True
    csv_text = Path(result["csv_path"]).read_text(encoding="utf-8")
    assert "22.1,58.4" in csv_text
