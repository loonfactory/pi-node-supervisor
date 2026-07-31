"""SFTP storage adapter with verified hosts and finite operations."""

from __future__ import annotations

import errno
import os
import posixpath
import stat
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import BinaryIO, Protocol

from capture_node.core.storages.contracts import (
    SaveResult,
    StorageListResult,
    StoredFile,
)
from capture_node.core.storages.options import SftpStorageOptions, StorageLayout
from capture_node.core.storages.paging import newest_page
from capture_node.core.storages.paths import (
    is_capture_date_directory,
    relative_storage_path,
    storage_read_paths,
    validate_storage_name,
)


class _SftpAttributes(Protocol):
    filename: str
    st_mode: int | None
    st_mtime: int | float | None
    st_size: int | None


class _SftpClient(Protocol):
    def open(self, filename: str, mode: str = "r") -> BinaryIO: ...

    def listdir_attr(self, path: str = ".") -> list[_SftpAttributes]: ...

    def lstat(self, path: str) -> _SftpAttributes: ...

    def stat(self, path: str) -> _SftpAttributes: ...

    def mkdir(self, path: str, mode: int = 0o777) -> None: ...

    def posix_rename(self, oldpath: str, newpath: str) -> None: ...

    def rename(self, oldpath: str, newpath: str) -> None: ...

    def remove(self, path: str) -> None: ...


_SessionFactory = Callable[[], AbstractContextManager[_SftpClient]]


class SftpStorage:
    """One-operation SFTP sessions confined to a configured remote directory."""

    @classmethod
    def from_options(cls, options: SftpStorageOptions) -> SftpStorage:
        return cls(
            options.name,
            options.remote_directory,
            _ParamikoSessionFactory(options, os.environ),
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
                self._require_directory(session)
                self._ensure_directory(session, posixpath.dirname(destination))
                try:
                    with session.open(temporary, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                    self._commit(session, temporary, destination)
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
                try:
                    attributes = session.lstat(path)
                except OSError as exc:
                    if self._is_missing(exc):
                        continue
                    raise
                if not self._is_regular_file(attributes):
                    raise FileNotFoundError(f"storage entry is not a file: {name}")
                with session.open(path, "rb") as stream:
                    return stream.read()
        raise FileNotFoundError(f"storage file does not exist: {name}")

    def list(self, page: int, page_size: int) -> StorageListResult:
        with self._session_factory() as session:
            self._require_directory(session)
            return newest_page(self._files(session), page, page_size)

    def _files(self, session: _SftpClient) -> Iterator[StoredFile]:
        root_entries = session.listdir_attr(self._remote_directory)
        for attributes in root_entries:
            stored = self._stored_file(attributes)
            if stored is not None:
                yield stored
                continue
            if not self._is_directory(attributes):
                continue
            if not is_capture_date_directory(attributes.filename):
                continue
            directory = posixpath.join(
                self._remote_directory,
                attributes.filename,
            )
            for nested in session.listdir_attr(directory):
                stored = self._stored_file(nested)
                if stored is not None:
                    yield stored

    def _require_directory(self, session: _SftpClient) -> None:
        attributes = session.stat(self._remote_directory)
        self._validate_directory(attributes, self._remote_directory)

    def _ensure_directory(self, session: _SftpClient, path: str) -> None:
        if path == self._remote_directory:
            return
        try:
            attributes = session.stat(path)
        except OSError as exc:
            if not self._is_missing(exc):
                raise
            try:
                session.mkdir(path, 0o755)
            except OSError as create_error:
                try:
                    attributes = session.stat(path)
                except OSError:
                    raise create_error
            else:
                attributes = session.stat(path)
        self._validate_directory(attributes, path)

    @staticmethod
    def _validate_directory(attributes: _SftpAttributes, path: str) -> None:
        if attributes.st_mode is not None and not stat.S_ISDIR(attributes.st_mode):
            raise NotADirectoryError(
                f"SFTP target is not a directory: {path}"
            )

    def _path(self, relative: PurePosixPath) -> str:
        return posixpath.join(self._remote_directory, relative.as_posix())

    @staticmethod
    def _commit(
        session: _SftpClient,
        temporary: str,
        destination: str,
    ) -> None:
        try:
            session.posix_rename(temporary, destination)
        except OSError:
            session.rename(temporary, destination)

    @staticmethod
    def _remove_temporary(session: _SftpClient, path: str) -> None:
        try:
            session.remove(path)
        except OSError:
            pass

    @staticmethod
    def _is_regular_file(attributes: _SftpAttributes) -> bool:
        return attributes.st_mode is not None and stat.S_ISREG(attributes.st_mode)

    @staticmethod
    def _is_directory(attributes: _SftpAttributes) -> bool:
        return attributes.st_mode is not None and stat.S_ISDIR(attributes.st_mode)

    @classmethod
    def _stored_file(cls, attributes: _SftpAttributes) -> StoredFile | None:
        try:
            validate_storage_name(attributes.filename)
        except ValueError:
            return None
        if (
            not cls._is_regular_file(attributes)
            or attributes.st_size is None
            or attributes.st_mtime is None
        ):
            return None
        return StoredFile(
            name=attributes.filename,
            size=int(attributes.st_size),
            modified_at=datetime.fromtimestamp(float(attributes.st_mtime), UTC),
        )

    @staticmethod
    def _is_missing(exc: OSError) -> bool:
        return isinstance(exc, FileNotFoundError) or exc.errno == errno.ENOENT


class _ParamikoSessionFactory:
    def __init__(
        self,
        options: SftpStorageOptions,
        environment: Mapping[str, str],
    ) -> None:
        self._options = options
        self._environment = environment

    @contextmanager
    def __call__(self) -> Iterator[_SftpClient]:
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError(
                "SFTP requires the capture-node[sftp] optional dependency"
            ) from exc

        options = self._options
        client = paramiko.SSHClient()
        try:
            client.load_host_keys(str(options.known_hosts.expanduser()))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(
                hostname=options.host,
                port=options.port,
                username=options.username,
                password=self._secret(options.password_env),
                key_filename=(
                    str(options.private_key.expanduser())
                    if options.private_key is not None
                    else None
                ),
                passphrase=self._secret(options.private_key_passphrase_env),
                timeout=options.connect_timeout_seconds,
                banner_timeout=options.connect_timeout_seconds,
                auth_timeout=options.connect_timeout_seconds,
                channel_timeout=options.operation_timeout_seconds,
                allow_agent=False,
                look_for_keys=False,
            )
            session = client.open_sftp()
            try:
                session.get_channel().settimeout(options.operation_timeout_seconds)
                yield session
            finally:
                session.close()
        finally:
            client.close()

    def _secret(self, variable: str | None) -> str | None:
        if variable is None:
            return None
        value = self._environment.get(variable)
        if not value:
            raise RuntimeError(
                f"SFTP secret environment variable is missing: {variable}"
            )
        return value
