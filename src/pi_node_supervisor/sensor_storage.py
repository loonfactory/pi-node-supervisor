"""Authoritative daily sensor CSV storage and atomic mirror delivery."""

from __future__ import annotations

import csv
import ftplib
import hashlib
import logging
import os
import shutil
import ssl
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .models import (
    FileStorageSettings,
    FtpsStorageSettings,
    SensorReading,
    SensorStorageSettings,
    SftpStorageSettings,
)
from .runtime_store import RuntimeStore

CSV_HEADER = ("timestamp", "temperature_c", "humidity_percent")
LOGGER = logging.getLogger(__name__)


class SensorMirror(Protocol):
    name: str
    def upload_atomic(self, local_path: Path, relative_path: PurePosixPath) -> None: ...


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


class SensorCsvRepository:
    def __init__(self, root: Path, node_id: str, timezone: str) -> None:
        self.root = root
        self.node_id = node_id
        self.zone = ZoneInfo(timezone)
        self._lock = threading.Lock()

    def commit(
        self, scheduled_at: datetime, reading: SensorReading
    ) -> tuple[Path, PurePosixPath, str]:
        if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
            raise ValueError("scheduled sensor slot must include a UTC offset")
        local = scheduled_at.astimezone(self.zone)
        relative = PurePosixPath(
            self.node_id,
            f"{local.year:04d}",
            f"{local.month:02d}",
            f"{local.date().isoformat()}.csv",
        )
        path = self.root.joinpath(*relative.parts)
        timestamp = local.isoformat()
        row = {
            "timestamp": timestamp,
            "temperature_c": format(reading.temperature_c, ".15g"),
            "humidity_percent": format(reading.humidity_percent, ".15g"),
        }
        with self._lock:
            rows = self._read(path)
            rows.setdefault(timestamp, row)
            self._write(path, [rows[key] for key in sorted(rows)])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return path, relative, digest

    @staticmethod
    def _read(path: Path) -> dict[str, dict[str, str]]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != CSV_HEADER:
                raise ValueError(f"sensor CSV has an invalid header: {path}")
            rows = list(reader)
        result = {}
        for row in rows:
            timestamp = row.get("timestamp")
            if not timestamp or timestamp in result or set(row) != set(CSV_HEADER):
                raise ValueError(f"sensor CSV has invalid or duplicate rows: {path}")
            result[timestamp] = {key: row[key] for key in CSV_HEADER}
        return result

    @staticmethod
    def _write(path: Path, rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=CSV_HEADER, lineterminator="\r\n")
                writer.writeheader()
                writer.writerows(rows)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)


class FileSensorMirror:
    def __init__(self, settings: FileStorageSettings) -> None:
        self.name = settings.name
        self.root = settings.directory

    def upload_atomic(self, local_path: Path, relative_path: PurePosixPath) -> None:
        _atomic_copy(local_path, self.root.joinpath(*relative_path.parts))


def _remote_paths(root: str, relative: PurePosixPath) -> tuple[str, str]:
    final = str(PurePosixPath(root).joinpath(relative))
    name = PurePosixPath(final).name
    temporary = str(PurePosixPath(final).with_name(f".{name}.uploading"))
    return temporary, final


class SftpSensorMirror:
    def __init__(self, settings: SftpStorageSettings) -> None:
        self.name = settings.name
        self.settings = settings

    def upload_atomic(self, local_path: Path, relative_path: PurePosixPath) -> None:
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("SFTP requires pi-node-supervisor[remote]") from exc
        client = paramiko.SSHClient()
        client.load_host_keys(self.settings.known_hosts.expanduser())
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        password = os.environ.get(self.settings.password_env) if self.settings.password_env else None
        passphrase = (
            os.environ.get(self.settings.private_key_passphrase_env)
            if self.settings.private_key_passphrase_env else None
        )
        client.connect(
            self.settings.host,
            port=self.settings.port,
            username=self.settings.username,
            password=password,
            key_filename=str(self.settings.private_key.expanduser()) if self.settings.private_key else None,
            passphrase=passphrase,
            timeout=self.settings.connect_timeout_seconds,
            banner_timeout=self.settings.connect_timeout_seconds,
            auth_timeout=self.settings.connect_timeout_seconds,
        )
        try:
            sftp = client.open_sftp()
            try:
                sftp.get_channel().settimeout(self.settings.operation_timeout_seconds)
                temporary, final = _remote_paths(self.settings.remote_directory, relative_path)
                current = PurePosixPath(final).parent
                parts = []
                while str(current) not in {"/", "."}:
                    parts.append(str(current))
                    current = current.parent
                for directory in reversed(parts):
                    try:
                        sftp.mkdir(directory)
                    except OSError:
                        pass
                sftp.put(str(local_path), temporary, confirm=True)
                replacer = getattr(sftp, "posix_rename", None)
                if not callable(replacer):
                    raise RuntimeError("SFTP server/client does not support atomic posix rename")
                replacer(temporary, final)
            finally:
                sftp.close()
        finally:
            client.close()


class FtpsSensorMirror:
    def __init__(self, settings: FtpsStorageSettings) -> None:
        self.name = settings.name
        self.settings = settings

    def upload_atomic(self, local_path: Path, relative_path: PurePosixPath) -> None:
        password = os.environ.get(self.settings.password_env)
        if password is None:
            raise RuntimeError(f"missing FTPS password environment: {self.settings.password_env}")
        context = ssl.create_default_context(cafile=self.settings.ca_file)
        if not self.settings.verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        ftp = ftplib.FTP_TLS(context=context, timeout=self.settings.connect_timeout_seconds)
        ftp.connect(self.settings.host, self.settings.port)
        if ftp.sock is not None:
            ftp.sock.settimeout(self.settings.operation_timeout_seconds)
        ftp.login(self.settings.username, password)
        ftp.prot_p()
        try:
            temporary, final = _remote_paths(self.settings.remote_directory, relative_path)
            directory = str(PurePosixPath(final).parent)
            current = ""
            for part in PurePosixPath(directory).parts:
                if part == "/":
                    current = "/"
                    continue
                current = str(PurePosixPath(current).joinpath(part))
                try:
                    ftp.mkd(current)
                except ftplib.error_perm:
                    pass
            with local_path.open("rb") as stream:
                ftp.storbinary(f"STOR {temporary}", stream)
            ftp.rename(temporary, final)
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()


class SensorStorageCoordinator:
    def __init__(
        self,
        *,
        node_id: str,
        timezone: str,
        settings: SensorStorageSettings,
        runtime: RuntimeStore,
        mirrors: dict[str, SensorMirror] | None = None,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.node_id = node_id
        self.settings = settings
        self.runtime = runtime
        self.clock = clock
        self.repository = SensorCsvRepository(settings.local_directory, node_id, timezone)
        self.mirrors = mirrors or {
            item.name: (
                FileSensorMirror(item) if isinstance(item, FileStorageSettings)
                else SftpSensorMirror(item) if isinstance(item, SftpStorageSettings)
                else FtpsSensorMirror(item)
            )
            for item in settings.mirrors
        }

    def commit(self, scheduled_at: datetime, reading: SensorReading) -> dict[str, Any]:
        path, relative, digest = self.repository.commit(scheduled_at, reading)
        self.runtime.append_event(
            "sensor.csv_committed",
            node_id=self.node_id,
            payload={"relative_path": str(relative), "scheduled_at": scheduled_at.isoformat()},
        )
        for target in self.mirrors:
            self.runtime.queue_sensor_delivery(
                node_id=self.node_id,
                target=target,
                relative_path=str(relative),
                content_sha256=digest,
            )
        delivery = self.sync_pending()
        return {"csv_path": str(path), "relative_path": str(relative), "delivery": delivery}

    def _safe_error(self, exc: Exception) -> str:
        message = f"{type(exc).__name__}: {exc}"
        for mirror in self.settings.mirrors:
            environment_names = [getattr(mirror, "password_env", None)]
            environment_names.append(
                getattr(mirror, "private_key_passphrase_env", None)
            )
            for environment_name in environment_names:
                secret = os.environ.get(environment_name) if environment_name else None
                if secret:
                    message = message.replace(secret, "***")
        return message[:2000]

    def sync_pending(self) -> dict[str, int]:
        result = {"succeeded": 0, "failed": 0, "backlog": 0}
        now = self.clock().astimezone(UTC)
        for item in self.runtime.due_sensor_deliveries(now):
            if item["node_id"] != self.node_id or item["target"] not in self.mirrors:
                continue
            relative = PurePosixPath(item["relative_path"])
            local_path = self.settings.local_directory.joinpath(*relative.parts)
            try:
                digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
                if digest != item["content_sha256"]:
                    raise RuntimeError("local authoritative CSV changed after delivery was queued")
                self.mirrors[item["target"]].upload_atomic(local_path, relative)
                self.runtime.complete_sensor_delivery(
                    node_id=self.node_id, target=item["target"], relative_path=str(relative)
                )
                self.runtime.append_event(
                    "sensor.mirror_succeeded", node_id=self.node_id,
                    payload={"target": item["target"], "relative_path": str(relative)},
                )
                result["succeeded"] += 1
            except Exception as exc:
                error = self._safe_error(exc)
                self.runtime.fail_sensor_delivery(
                    node_id=self.node_id,
                    target=item["target"],
                    relative_path=str(relative),
                    error=error,
                    retry_base_seconds=self.settings.retry_base_seconds,
                    retry_max_seconds=self.settings.retry_max_seconds,
                    now=now,
                )
                self.runtime.append_event(
                    "sensor.mirror_failed", severity="warning", node_id=self.node_id,
                    payload={"target": item["target"], "relative_path": str(relative), "error": error},
                )
                LOGGER.warning(
                    "sensor mirror failed node=%s target=%s relative_path=%s error=%s",
                    self.node_id,
                    item["target"],
                    relative,
                    error,
                )
                result["failed"] += 1
        status = self.runtime.sensor_delivery_status()["counts"]
        result["backlog"] = status["pending"] + status["failed"]
        return result
