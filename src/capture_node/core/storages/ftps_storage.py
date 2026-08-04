"""Explicit FTPS storage adapter with verified TLS data channels."""

from __future__ import annotations

import logging
import os
import posixpath
import ssl
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from ftplib import CRLF, FTP_TLS, Error, all_errors, error_perm
from io import BytesIO
from pathlib import PurePosixPath
from typing import Protocol

from capture_node.core.storages.contracts import (
    SaveResult,
    StorageListResult,
    StoredFile,
)
from capture_node.core.storages.options import FtpsStorageOptions, StorageLayout
from capture_node.core.storages.paging import newest_page
from capture_node.core.storages.paths import (
    is_capture_date_directory,
    relative_storage_path,
    storage_read_paths,
    validate_storage_name,
)

logger = logging.getLogger(__name__)


class _FtpsClient(Protocol):
    def cwd(self, dirname: str) -> str: ...

    def delete(self, filename: str) -> str: ...

    def mkd(self, pathname: str) -> str: ...

    def mlsd(
        self,
        path: str = "",
        facts: list[str] | None = None,
    ) -> Iterator[tuple[str, dict[str, str]]]: ...

    def nlst(self, *args: str) -> list[str]: ...

    def pwd(self) -> str: ...

    def rename(self, fromname: str, toname: str) -> str: ...

    def retrbinary(
        self,
        cmd: str,
        callback: Callable[[bytes], object],
    ) -> str: ...

    def storbinary(self, cmd: str, fp: BytesIO) -> str: ...

    def sendcmd(self, cmd: str) -> str: ...

    def size(self, filename: str) -> int | None: ...


_SessionFactory = Callable[[], AbstractContextManager[_FtpsClient]]
_DATA_TLS_SHUTDOWN_TIMEOUT_SECONDS = 1.0


class FtpsStorage:
    """One-operation explicit FTPS sessions under one configured root."""

    @classmethod
    def from_options(cls, options: FtpsStorageOptions) -> FtpsStorage:
        return cls(
            options.name,
            options.remote_directory,
            _FtpsSessionFactory(options, os.environ),
            options.layout,
        )

    def __init__(
        self,
        name: str,
        remote_directory: str,
        session_factory: _SessionFactory,
        layout: StorageLayout = "daily",
    ) -> None:
        self._name = name
        self._remote_directory = remote_directory
        self._session_factory = session_factory
        self._layout = layout

    @property
    def name(self) -> str:
        return self._name

    def save(self, data: bytes, name: str) -> SaveResult:
        try:
            relative = relative_storage_path(name, self._layout)
            destination = self._path(relative)
            temporary = self._path(
                relative.with_name(f".{name}.{uuid.uuid4().hex}.tmp")
            )
            with self._session_factory() as session:
                self._ensure_remote_directory(session)
                self._ensure_directory(session, posixpath.dirname(destination))
                try:
                    session.storbinary(f"STOR {temporary}", BytesIO(data))
                    stored_size = session.size(temporary)
                    if stored_size is not None and stored_size != len(data):
                        raise OSError(
                            f"FTPS stored size mismatch: "
                            f"expected {len(data)}, got {stored_size}"
                        )
                    session.rename(temporary, destination)
                except BaseException:
                    self._remove_temporary(session, temporary)
                    raise
            return SaveResult.success()
        except BaseException as exc:
            return SaveResult.failure(exc)

    def read(self, name: str) -> bytes:
        with self._session_factory() as session:
            for relative in storage_read_paths(name):
                path = self._path(relative)
                output = BytesIO()
                try:
                    expected_size = session.size(path)
                    session.retrbinary(f"RETR {path}", output.write)
                except error_perm as exc:
                    if self._is_missing(exc):
                        continue
                    raise
                data = output.getvalue()
                if expected_size is not None and len(data) != expected_size:
                    raise OSError(
                        f"FTPS read size mismatch: "
                        f"expected {expected_size}, got {len(data)}"
                    )
                return data
        raise FileNotFoundError(f"storage file does not exist: {name}")

    def list(self, page: int, page_size: int) -> StorageListResult:
        with self._session_factory() as session:
            self._require_directory(session)
            return newest_page(self._files(session), page, page_size)

    def delete(self, name: str) -> None:
        with self._session_factory() as session:
            for relative in storage_read_paths(name):
                path = self._path(relative)
                try:
                    session.delete(path)
                    return
                except error_perm as exc:
                    if self._is_missing(exc):
                        continue
                    raise
        raise FileNotFoundError(f"storage file does not exist: {name}")

    def _files(self, session: _FtpsClient) -> Iterator[StoredFile]:
        directories: list[str] = []
        for name, facts in self._entries(session, self._remote_directory):
            stored = self._stored_file(name, facts)
            if stored is not None:
                yield stored
                continue
            if (
                facts.get("type", "").lower() == "dir"
                and is_capture_date_directory(name)
            ):
                directories.append(name)
        for name in directories:
            directory = posixpath.join(self._remote_directory, name)
            for nested_name, nested_facts in self._entries(session, directory):
                stored = self._stored_file(nested_name, nested_facts)
                if stored is not None:
                    yield stored

    @staticmethod
    def _entries(
        session: _FtpsClient,
        path: str,
    ) -> Iterator[tuple[str, dict[str, str]]]:
        try:
            yield from session.mlsd(path)
            return
        except error_perm as exc:
            if not _is_unsupported_command(exc):
                raise
        yield from _fallback_entries(session, path)

    def _require_directory(self, session: _FtpsClient) -> None:
        try:
            session.cwd(self._remote_directory)
        except error_perm as exc:
            raise NotADirectoryError(
                f"FTPS target is not a directory: {self._remote_directory}"
            ) from exc

    def _ensure_remote_directory(self, session: _FtpsClient) -> None:
        current = "/"
        self._ensure_directory(session, current)
        for part in PurePosixPath(self._remote_directory).parts[1:]:
            current = posixpath.join(current, part)
            if self._ensure_directory(session, current):
                logger.info("created FTPS remote directory: %s", current)

    def _ensure_directory(self, session: _FtpsClient, path: str) -> bool:
        try:
            session.cwd(path)
            return False
        except error_perm as exc:
            if not self._is_missing(exc):
                raise
        try:
            session.mkd(path)
        except error_perm as create_error:
            try:
                session.cwd(path)
            except error_perm:
                raise create_error
            return False
        session.cwd(path)
        return True

    def _path(self, relative: PurePosixPath) -> str:
        return posixpath.join(self._remote_directory, relative.as_posix())

    @staticmethod
    def _remove_temporary(session: _FtpsClient, path: str) -> None:
        try:
            session.delete(path)
        except all_errors:
            pass

    @staticmethod
    def _stored_file(name: str, facts: Mapping[str, str]) -> StoredFile | None:
        try:
            validate_storage_name(name)
            if facts.get("type", "").lower() != "file":
                return None
            size = int(facts["size"])
            modified = _parse_ftp_time(facts["modify"])
        except (KeyError, TypeError, ValueError):
            return None
        return StoredFile(name=name, size=size, modified_at=modified)

    @staticmethod
    def _is_missing(exc: error_perm) -> bool:
        return str(exc).startswith("550")


class _FtpsSessionFactory:
    def __init__(
        self,
        options: FtpsStorageOptions,
        environment: Mapping[str, str],
    ) -> None:
        self._options = options
        self._environment = environment

    @contextmanager
    def __call__(self) -> Iterator[_FtpsClient]:
        options = self._options
        context = _create_tls_context(options)
        client = _CompatibleFTP_TLS(
            context=context,
            timeout=options.connect_timeout_seconds,
            encoding="utf-8",
        )
        try:
            client.connect(
                options.host,
                options.port,
                timeout=options.connect_timeout_seconds,
            )
            client.login(options.username, self._secret(options.password_env))
            client.prot_p()
            client.timeout = options.operation_timeout_seconds
            if client.sock is not None:
                client.sock.settimeout(options.operation_timeout_seconds)
            yield client
            try:
                client.quit()
            except all_errors:
                client.close()
        finally:
            client.close()

    def _secret(self, variable: str) -> str:
        value = self._environment.get(variable)
        if not value:
            raise RuntimeError(
                f"FTPS secret environment variable is missing: {variable}"
            )
        return value


def _parse_ftp_time(value: str) -> datetime:
    whole_seconds = value.split(".", 1)[0]
    return datetime.strptime(whole_seconds, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def _fallback_entries(
    session: _FtpsClient,
    path: str,
) -> Iterator[tuple[str, dict[str, str]]]:
    for listed_name in session.nlst(path):
        name = PurePosixPath(listed_name.rstrip("/")).name
        try:
            validate_storage_name(name)
        except ValueError:
            continue
        entry_path = posixpath.join(path, name)
        try:
            size = session.size(entry_path)
        except error_perm:
            try:
                session.cwd(entry_path)
            except error_perm:
                continue
            yield name, {"type": "dir"}
            continue
        if size is None:
            continue
        try:
            response = session.sendcmd(f"MDTM {entry_path}")
        except error_perm:
            continue
        if not response.startswith("213 "):
            continue
        yield name, {
            "type": "file",
            "size": str(size),
            "modify": response[4:].strip(),
        }


def _is_unsupported_command(exc: error_perm) -> bool:
    return str(exc)[:3] in {"500", "501", "502", "504"}


def _create_tls_context(options: FtpsStorageOptions) -> ssl.SSLContext:
    if options.verify_tls:
        return ssl.create_default_context(
            cafile=(
                str(options.ca_file.expanduser())
                if options.ca_file is not None
                else None
            )
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class _CompatibleFTP_TLS(FTP_TLS):
    """Avoid indefinite waits for servers that omit data-channel close_notify."""

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
        if callback is None:
            callback = print
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
        fp: BytesIO,
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
