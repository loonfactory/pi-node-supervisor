"""Independent atomic Storage targets shared by camera and sensor runs."""

from __future__ import annotations

import csv
import ftplib
import io
import os
import posixpath
import shutil
import ssl
import stat
import tempfile
from dataclasses import dataclass
from ftplib import CRLF, Error
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Protocol

from .config_v2 import ResolvedStorage


@dataclass(frozen=True)
class SaveResult:
    target: str
    path: str
    succeeded: bool
    size: int | None = None
    error: str | None = None


class StorageTarget(Protocol):
    name: str

    def save_atomic(self, source: Path, relative_path: PurePosixPath) -> SaveResult: ...
    def read_bytes(self, relative_path: PurePosixPath) -> bytes | None: ...
    def save_bytes_atomic(self, data: bytes, relative_path: PurePosixPath) -> SaveResult: ...
    def cleanup_temporary(self) -> int: ...


def _safe_relative(value: PurePosixPath) -> PurePosixPath:
    if value.is_absolute() or not value.parts or ".." in value.parts or "\x00" in str(value):
        raise ValueError(f"unsafe Storage relative path: {value}")
    return value


class LocalStorageTarget:
    def __init__(
        self, storage: ResolvedStorage, managed_root: Path | None = None
    ) -> None:
        self.name = storage.name
        if managed_root is None:
            self.root = Path(storage.final_path)
        else:
            root = managed_root.resolve()
            logical = PurePosixPath(storage.final_path)
            candidate = root.joinpath(*logical.parts[1:]).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError("local Storage path escapes the managed root")
            self.root = candidate

    def save_atomic(self, source: Path, relative_path: PurePosixPath) -> SaveResult:
        relative = _safe_relative(relative_path)
        destination = self.root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".uploading", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temporary, destination)
            _sync_directory(destination.parent)
            return SaveResult(
                target=self.name,
                path=str(destination),
                succeeded=True,
                size=destination.stat().st_size,
            )
        finally:
            temporary.unlink(missing_ok=True)

    def read_bytes(self, relative_path: PurePosixPath) -> bytes | None:
        path = self.root.joinpath(*_safe_relative(relative_path).parts)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def save_bytes_atomic(self, data: bytes, relative_path: PurePosixPath) -> SaveResult:
        descriptor, name = tempfile.mkstemp(prefix="sensor-row-", suffix=".tmp")
        source = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            return self.save_atomic(source, relative_path)
        finally:
            source.unlink(missing_ok=True)

    def cleanup_temporary(self) -> int:
        if not self.root.is_dir():
            return 0
        removed = 0
        for path in self.root.rglob(".*.uploading"):
            if path.is_file() and not path.is_symlink():
                path.unlink()
                removed += 1
        return removed


class SftpStorageTarget:
    def __init__(self, storage: ResolvedStorage) -> None:
        self.name = storage.name
        self.root = storage.final_path
        self.settings = storage.settings

    def _connect(self):
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("SFTP requires pi-node-supervisor[remote]") from exc
        client = paramiko.SSHClient()
        known_hosts = self.settings.get("knownHostsPath")
        if known_hosts:
            client.load_host_keys(str(Path(known_hosts).expanduser()))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        password_env = self.settings.get("passwordEnv")
        passphrase_env = self.settings.get("privateKeyPassphraseEnv")
        client.connect(
            self.settings["host"],
            port=self.settings.get("port", 22),
            username=self.settings["username"],
            password=os.environ.get(password_env) if password_env else None,
            key_filename=self.settings.get("privateKeyPath"),
            passphrase=os.environ.get(passphrase_env) if passphrase_env else None,
            timeout=self.settings.get("connectTimeoutSeconds", 10),
            banner_timeout=self.settings.get("connectTimeoutSeconds", 10),
            auth_timeout=self.settings.get("connectTimeoutSeconds", 10),
        )
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(self.settings.get("operationTimeoutSeconds", 120))
        return client, sftp

    def save_atomic(self, source: Path, relative_path: PurePosixPath) -> SaveResult:
        final = _remote_final(self.root, relative_path)
        temporary = _temporary_remote(final)
        client, sftp = self._connect()
        try:
            _sftp_mkdirs(sftp, str(PurePosixPath(final).parent))
            try:
                sftp.put(str(source), temporary, confirm=True)
                replacer = getattr(sftp, "posix_rename", None)
                if not callable(replacer):
                    raise RuntimeError("SFTP server does not support atomic posix rename")
                replacer(temporary, final)
            except BaseException:
                try:
                    sftp.remove(temporary)
                except OSError:
                    pass
                raise
            return SaveResult(
                target=self.name,
                path=final,
                succeeded=True,
                size=source.stat().st_size,
            )
        finally:
            sftp.close()
            client.close()

    def read_bytes(self, relative_path: PurePosixPath) -> bytes | None:
        final = _remote_final(self.root, relative_path)
        client, sftp = self._connect()
        try:
            with io.BytesIO() as output:
                try:
                    sftp.getfo(final, output)
                except OSError as exc:
                    if getattr(exc, "errno", None) == 2:
                        return None
                    raise
                return output.getvalue()
        finally:
            sftp.close()
            client.close()

    def save_bytes_atomic(self, data: bytes, relative_path: PurePosixPath) -> SaveResult:
        return _save_temporary_bytes(self, data, relative_path)

    def cleanup_temporary(self) -> int:
        client, sftp = self._connect()
        try:
            return _sftp_cleanup(sftp, self.root)
        finally:
            sftp.close()
            client.close()


class FtpsStorageTarget:
    def __init__(self, storage: ResolvedStorage) -> None:
        self.name = storage.name
        self.root = storage.final_path
        self.settings = storage.settings

    def _connect(self):
        password_env = self.settings["passwordEnv"]
        password = os.environ.get(password_env)
        if password is None:
            raise RuntimeError(f"missing FTPS password environment: {password_env}")
        context = ssl.create_default_context(cafile=self.settings.get("caFilePath"))
        if not self.settings.get("verifyTls", True):
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        ftp = _CompatibleFTP_TLS(
            context=context, timeout=self.settings.get("connectTimeoutSeconds", 10)
        )
        ftp.connect(self.settings["host"], self.settings.get("port", 21))
        if ftp.sock is not None:
            ftp.sock.settimeout(self.settings.get("operationTimeoutSeconds", 120))
        ftp.login(self.settings["username"], password)
        ftp.prot_p()
        return ftp

    def save_atomic(self, source: Path, relative_path: PurePosixPath) -> SaveResult:
        final = _remote_final(self.root, relative_path)
        temporary = _temporary_remote(final)
        backup = _replacement_backup(final)
        ftp = self._connect()
        try:
            _ftps_mkdirs(ftp, str(PurePosixPath(final).parent))
            try:
                with source.open("rb") as stream:
                    ftp.storbinary(f"STOR {temporary}", stream)
                _ftps_replace(ftp, temporary, final, backup)
            except BaseException:
                try:
                    ftp.delete(temporary)
                except ftplib.all_errors:
                    pass
                raise
            return SaveResult(
                target=self.name,
                path=final,
                succeeded=True,
                size=source.stat().st_size,
            )
        finally:
            _close_ftp(ftp)

    def read_bytes(self, relative_path: PurePosixPath) -> bytes | None:
        final = _remote_final(self.root, relative_path)
        ftp = self._connect()
        output = io.BytesIO()
        try:
            try:
                ftp.retrbinary(f"RETR {final}", output.write)
            except ftplib.error_perm as exc:
                if str(exc).startswith("550"):
                    return None
                raise
            return output.getvalue()
        finally:
            _close_ftp(ftp)

    def save_bytes_atomic(self, data: bytes, relative_path: PurePosixPath) -> SaveResult:
        return _save_temporary_bytes(self, data, relative_path)

    def cleanup_temporary(self) -> int:
        ftp = self._connect()
        try:
            return _ftps_cleanup(ftp, self.root)
        finally:
            _close_ftp(ftp)


class StoragePipeline:
    def __init__(self, targets: tuple[StorageTarget, ...]) -> None:
        self.targets = targets

    @classmethod
    def from_resolved(
        cls,
        storages: tuple[ResolvedStorage, ...],
        *,
        local_root: Path | None = None,
    ) -> StoragePipeline:
        return cls(tuple(build_storage_target(item, local_root=local_root) for item in storages))

    def save_file(
        self, source: Path, relative_path: PurePosixPath
    ) -> tuple[SaveResult, ...]:
        results: list[SaveResult] = []
        for target in self.targets:
            try:
                results.append(target.save_atomic(source, relative_path))
            except Exception as exc:
                results.append(
                    SaveResult(
                        target=target.name,
                        path=str(relative_path),
                        succeeded=False,
                        error=_safe_error(exc),
                    )
                )
        return tuple(results)

    def append_sensor_row(
        self,
        relative_path: PurePosixPath,
        *,
        header: tuple[str, ...],
        row: tuple[object, ...],
    ) -> tuple[SaveResult, ...]:
        encoded_header = _csv_line(header)
        encoded_row = _csv_line(row)
        results: list[SaveResult] = []
        for target in self.targets:
            try:
                current = target.read_bytes(relative_path)
                if current is None:
                    updated = encoded_header + encoded_row
                else:
                    if not current.startswith(encoded_header):
                        raise ValueError("sensor CSV header does not match")
                    updated = current + (b"" if current.endswith(b"\n") else b"\r\n") + encoded_row
                results.append(target.save_bytes_atomic(updated, relative_path))
            except Exception as exc:
                results.append(
                    SaveResult(
                        target=target.name,
                        path=str(relative_path),
                        succeeded=False,
                        error=_safe_error(exc),
                    )
                )
        return tuple(results)

    def cleanup_temporary(self) -> tuple[SaveResult, ...]:
        results = []
        for target in self.targets:
            try:
                removed = target.cleanup_temporary()
                results.append(SaveResult(target.name, "*.uploading", True, removed))
            except Exception as exc:
                results.append(
                    SaveResult(target.name, "*.uploading", False, error=_safe_error(exc))
                )
        return tuple(results)


def build_storage_target(
    storage: ResolvedStorage, *, local_root: Path | None = None
) -> StorageTarget:
    if storage.type == "local":
        return LocalStorageTarget(storage, local_root)
    if storage.type == "sftp":
        return SftpStorageTarget(storage)
    return FtpsStorageTarget(storage)


def _csv_line(values: tuple[object, ...]) -> bytes:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerow(values)
    return output.getvalue().encode("utf-8")


def _save_temporary_bytes(
    target: StorageTarget, data: bytes, relative_path: PurePosixPath
) -> SaveResult:
    descriptor, name = tempfile.mkstemp(prefix="storage-content-", suffix=".tmp")
    source = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return target.save_atomic(source, relative_path)
    finally:
        source.unlink(missing_ok=True)


def _remote_final(root: str, relative_path: PurePosixPath) -> str:
    relative = _safe_relative(relative_path)
    return posixpath.join(root, *relative.parts)


def _temporary_remote(final: str) -> str:
    path = PurePosixPath(final)
    return str(path.with_name(f".{path.name}.{os.getpid()}.uploading"))


def _replacement_backup(final: str) -> str:
    path = PurePosixPath(final)
    return str(path.with_name(f".{path.name}.{os.getpid()}.replacing"))


def _sftp_mkdirs(sftp, directory: str) -> None:
    current = PurePosixPath(directory)
    parts: list[str] = []
    while str(current) not in {"/", "."}:
        parts.append(str(current))
        current = current.parent
    for item in reversed(parts):
        try:
            sftp.mkdir(item)
        except OSError:
            pass


def _sftp_cleanup(sftp, directory: str) -> int:
    removed = 0
    try:
        entries = sftp.listdir_attr(directory)
    except OSError as exc:
        if getattr(exc, "errno", None) == 2:
            return 0
        raise
    for entry in entries:
        path = posixpath.join(directory, entry.filename)
        if stat.S_ISDIR(entry.st_mode):
            removed += _sftp_cleanup(sftp, path)
        elif entry.filename.startswith(".") and entry.filename.endswith(".uploading"):
            sftp.remove(path)
            removed += 1
    return removed


def _ftps_mkdirs(ftp, directory: str) -> None:
    current = "/"
    for part in PurePosixPath(directory).parts:
        if part == "/":
            continue
        current = str(PurePosixPath(current).joinpath(part))
        try:
            ftp.mkd(current)
        except ftplib.error_perm:
            pass


def _ftps_cleanup(ftp, directory: str) -> int:
    removed = 0
    try:
        entries = list(ftp.mlsd(directory))
    except ftplib.error_perm as exc:
        if str(exc).startswith("550"):
            return 0
        if not _ftps_command_unsupported(exc):
            raise
        entries = list(_ftps_fallback_entries(ftp, directory))
    files: list[tuple[str, str]] = []
    for name, facts in entries:
        path = str(PurePosixPath(directory) / name)
        if facts.get("type") == "dir":
            removed += _ftps_cleanup(ftp, path)
        elif facts.get("type") == "file":
            files.append((name, path))
    for name, path in files:
        if name.startswith(".") and name.endswith(".replacing"):
            final_name = _replacement_final_name(name)
            if final_name is None:
                continue
            final = str(PurePosixPath(directory) / final_name)
            if _ftps_file_exists(ftp, final):
                ftp.delete(path)
            else:
                ftp.rename(path, final)
            removed += 1
    for name, path in files:
        if name.startswith(".") and name.endswith(".uploading"):
            ftp.delete(path)
            removed += 1
    return removed


def _ftps_replace(ftp, temporary: str, final: str, backup: str) -> None:
    """Replace a file on servers whose RNTO cannot overwrite an existing path."""
    existing_moved = False
    try:
        try:
            ftp.rename(final, backup)
            existing_moved = True
        except ftplib.error_perm as exc:
            if not str(exc).startswith("550"):
                raise
        ftp.rename(temporary, final)
        if existing_moved:
            ftp.delete(backup)
    except BaseException:
        if existing_moved:
            try:
                ftp.delete(final)
            except ftplib.all_errors:
                pass
            try:
                ftp.rename(backup, final)
            except ftplib.all_errors:
                pass
        raise


def _replacement_final_name(name: str) -> str | None:
    if not name.startswith(".") or not name.endswith(".replacing"):
        return None
    body = name[1 : -len(".replacing")]
    final_name, separator, process_id = body.rpartition(".")
    if not separator or not final_name or not process_id.isdigit():
        return None
    return final_name


def _ftps_file_exists(ftp, path: str) -> bool:
    try:
        ftp.size(path)
        return True
    except ftplib.error_perm as exc:
        if str(exc).startswith("550"):
            return False
        raise


def _ftps_fallback_entries(ftp, directory: str):
    """List entries on older FTP servers that do not implement MLSD."""
    try:
        listed = ftp.nlst(directory)
    except ftplib.error_perm as exc:
        if str(exc).startswith("550"):
            return
        raise
    for listed_name in listed:
        name = PurePosixPath(listed_name.rstrip("/")).name
        if name in {"", ".", ".."}:
            continue
        path = posixpath.join(directory, name)
        try:
            ftp.size(path)
        except ftplib.error_perm:
            try:
                ftp.cwd(path)
            except ftplib.error_perm:
                continue
            yield name, {"type": "dir"}
        else:
            yield name, {"type": "file"}


def _ftps_command_unsupported(exc: ftplib.error_perm) -> bool:
    return str(exc)[:3] in {"500", "501", "502", "504"}


_DATA_TLS_SHUTDOWN_TIMEOUT_SECONDS = 1.0


class _CompatibleFTP_TLS(ftplib.FTP_TLS):
    """Tolerate FTPS servers that omit the data-channel TLS close notification."""

    def retrbinary(
        self,
        cmd: str,
        callback: Callable[[bytes], object],
        blocksize: int = 8192,
        rest: str | None = None,
    ) -> str:
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as connection:
            while block := connection.recv(blocksize):
                callback(block)
            _finish_tls_data_channel(connection)
        return self.voidresp()

    def retrlines(
        self,
        cmd: str,
        callback: Callable[[str], object] | None = None,
    ) -> str:
        callback = callback or print
        self.sendcmd("TYPE A")
        with self.transfercmd(cmd) as connection:
            with connection.makefile("r", encoding=self.encoding) as stream:
                while True:
                    line = stream.readline(self.maxline + 1)
                    if len(line) > self.maxline:
                        raise Error(f"got more than {self.maxline} bytes")
                    if not line:
                        break
                    if line.endswith(CRLF):
                        line = line[:-2]
                    elif line.endswith("\n"):
                        line = line[:-1]
                    callback(line)
            _finish_tls_data_channel(connection)
        return self.voidresp()

    def storbinary(
        self,
        cmd: str,
        fp: BinaryIO,
        blocksize: int = 8192,
        callback: Callable[[bytes], object] | None = None,
        rest: str | None = None,
    ) -> str:
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as connection:
            while block := fp.read(blocksize):
                connection.sendall(block)
                if callback is not None:
                    callback(block)
            _finish_tls_data_channel(connection)
        return self.voidresp()


def _finish_tls_data_channel(connection: object) -> None:
    if not isinstance(connection, ssl.SSLSocket):
        return
    current_timeout = connection.gettimeout()
    shutdown_timeout = _DATA_TLS_SHUTDOWN_TIMEOUT_SECONDS
    if current_timeout is not None:
        shutdown_timeout = min(current_timeout, shutdown_timeout)
    connection.settimeout(shutdown_timeout)
    try:
        raw_socket = connection.unwrap()
    except (TimeoutError, ssl.SSLEOFError):
        return
    raw_socket.close()


def _close_ftp(ftp) -> None:
    try:
        ftp.quit()
    except ftplib.all_errors:
        ftp.close()


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:2000]
