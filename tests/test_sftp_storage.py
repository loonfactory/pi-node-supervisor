from __future__ import annotations

import io
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from capture_node.core.storages import SftpStorageOptions
from capture_node.core.storages.sftp_storage import SftpStorage


@dataclass
class FakeAttributes:
    filename: str
    st_mode: int | None
    st_mtime: int | float | None
    st_size: int | None


@dataclass
class FakeRemoteFile:
    data: bytes
    modified: float
    mode: int = stat.S_IFREG | 0o644


class WriteBuffer(io.BytesIO):
    def __init__(self, complete) -> None:
        super().__init__()
        self._complete = complete

    def close(self) -> None:
        if not self.closed:
            self._complete(self.getvalue())
        super().close()


class FakeChannel:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout


class FakeSftpClient:
    def __init__(self, root: str = "/captures") -> None:
        self.root = root
        self.files: dict[str, FakeRemoteFile] = {}
        self.directories = {root}
        self.channel = FakeChannel()
        self.closed = False
        self.posix_rename_supported = True
        self.fail_rename = False
        self.standard_rename_calls = 0

    def open(self, filename: str, mode: str = "r"):
        if "w" in mode:
            return WriteBuffer(
                lambda data: self.files.__setitem__(
                    filename,
                    FakeRemoteFile(data, 100),
                )
            )
        return io.BytesIO(self.files[filename].data)

    def listdir_attr(self, path: str = ".") -> list[FakeAttributes]:
        prefix = f"{path.rstrip('/')}/"
        files = [
            self._attributes(remote_path, remote_file)
            for remote_path, remote_file in self.files.items()
            if remote_path.startswith(prefix)
            and "/" not in remote_path[len(prefix) :]
        ]
        directories = [
            FakeAttributes(
                remote_path.rsplit("/", 1)[-1],
                stat.S_IFDIR | 0o755,
                0,
                0,
            )
            for remote_path in self.directories
            if remote_path.startswith(prefix)
            and "/" not in remote_path[len(prefix) :]
        ]
        return files + directories

    def lstat(self, path: str) -> FakeAttributes:
        try:
            return self._attributes(path, self.files[path])
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def stat(self, path: str) -> FakeAttributes:
        if path in self.directories:
            return FakeAttributes(
                path.rsplit("/", 1)[-1],
                stat.S_IFDIR | 0o755,
                0,
                0,
            )
        return self.lstat(path)

    def mkdir(self, path: str, mode: int = 0o777) -> None:
        if path in self.directories:
            raise FileExistsError(path)
        self.directories.add(path)

    def posix_rename(self, oldpath: str, newpath: str) -> None:
        if not self.posix_rename_supported:
            raise OSError("posix rename is unsupported")
        if self.fail_rename:
            raise OSError("rename failed")
        self.files[newpath] = self.files.pop(oldpath)

    def rename(self, oldpath: str, newpath: str) -> None:
        self.standard_rename_calls += 1
        if self.fail_rename:
            raise OSError("rename failed")
        self.files[newpath] = self.files.pop(oldpath)

    def remove(self, path: str) -> None:
        self.files.pop(path, None)

    def get_channel(self) -> FakeChannel:
        return self.channel

    def close(self) -> None:
        self.closed = True

    @staticmethod
    def _attributes(
        path: str,
        remote_file: FakeRemoteFile,
    ) -> FakeAttributes:
        return FakeAttributes(
            path.rsplit("/", 1)[-1],
            remote_file.mode,
            remote_file.modified,
            len(remote_file.data),
        )


def session_factory(client: FakeSftpClient):
    @contextmanager
    def session():
        yield client

    return session


def test_sftp_storage_saves_reads_and_pages_remote_files() -> None:
    client = FakeSftpClient()
    client.files["/captures/older.jpg"] = FakeRemoteFile(b"old", 10)
    storage = SftpStorage("remote", "/captures", session_factory(client))

    saved = storage.save(b"new", "newer.jpg")
    client.files["/captures/newer.jpg"].modified = 20
    listing = storage.list(1, 1)

    assert saved.succeeded
    assert storage.read("newer.jpg") == b"new"
    assert listing.total == 2
    assert listing.items[0].name == "newer.jpg"
    assert listing.items[0].size == 3
    assert not any(path.endswith(".tmp") for path in client.files)


def test_sftp_storage_uses_daily_directories_and_reads_legacy_files() -> None:
    client = FakeSftpClient()
    name = "20260731T131120.000000Z_capture.jpg"
    client.files["/captures/legacy.jpg"] = FakeRemoteFile(b"legacy", 10)
    storage = SftpStorage("remote", "/captures", session_factory(client))

    result = storage.save(b"daily", name)
    listing = storage.list(1, 10)

    assert result.succeeded
    assert f"/captures/20260731/{name}" in client.files
    assert storage.read(name) == b"daily"
    assert storage.read("legacy.jpg") == b"legacy"
    assert {item.name for item in listing.items} == {name, "legacy.jpg"}

    storage.delete(name)

    assert f"/captures/20260731/{name}" not in client.files
    with pytest.raises(FileNotFoundError):
        storage.delete(name)


def test_sftp_storage_falls_back_to_standard_rename() -> None:
    client = FakeSftpClient()
    client.posix_rename_supported = False
    storage = SftpStorage("remote", "/captures", session_factory(client))

    result = storage.save(b"capture", "capture.jpg")

    assert result.succeeded
    assert client.standard_rename_calls == 1
    assert client.files["/captures/capture.jpg"].data == b"capture"


def test_sftp_storage_cleans_temporary_file_after_upload_failure() -> None:
    client = FakeSftpClient()
    client.fail_rename = True
    storage = SftpStorage("remote", "/captures", session_factory(client))

    result = storage.save(b"capture", "capture.jpg")

    assert not result.succeeded
    assert isinstance(result.cause, OSError)
    assert client.files == {}


def test_sftp_storage_rejects_escape_and_skips_symbolic_links() -> None:
    client = FakeSftpClient()
    client.files["/captures/link.jpg"] = FakeRemoteFile(
        b"outside",
        10,
        stat.S_IFLNK | 0o777,
    )
    storage = SftpStorage("remote", "/captures", session_factory(client))

    assert storage.list(1, 10).items == ()
    with pytest.raises(ValueError, match="filename"):
        storage.read("../secret")
    with pytest.raises(FileNotFoundError, match="not a file"):
        storage.read("link.jpg")


def test_sftp_options_reference_secrets_without_storing_them() -> None:
    options = SftpStorageOptions(
        type="sftp",
        name="remote",
        host="storage.local",
        username="capture-node",
        remote_directory="/captures",
        password_env="CAPTURE_NODE_SFTP_PASSWORD",
    )

    document = options.model_dump(mode="json")

    assert document["layout"] == "daily"
    assert document["password_env"] == "CAPTURE_NODE_SFTP_PASSWORD"
    assert "password" not in document
    with pytest.raises(ValidationError, match="private_key or password_env"):
        SftpStorageOptions(
            type="sftp",
            name="remote",
            host="storage.local",
            username="capture-node",
            remote_directory="/captures",
        )
    with pytest.raises(ValidationError, match="normalized absolute"):
        SftpStorageOptions(
            type="sftp",
            name="remote",
            host="storage.local",
            username="capture-node",
            remote_directory="../captures",
            password_env="SFTP_PASSWORD",
        )
    with pytest.raises(ValidationError):
        SftpStorageOptions.model_validate({**document, "password": "secret"})
    with pytest.raises(ValidationError):
        SftpStorageOptions.model_validate({**document, "layout": "monthly"})


def test_paramiko_session_uses_environment_secret_and_finite_timeouts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sftp = FakeSftpClient()
    clients = []

    class FakeSshClient:
        def __init__(self) -> None:
            self.host_keys: str | None = None
            self.policy = None
            self.connect_arguments = {}
            self.closed = False
            clients.append(self)

        def load_host_keys(self, filename: str) -> None:
            self.host_keys = filename

        def set_missing_host_key_policy(self, policy) -> None:
            self.policy = policy

        def connect(self, **kwargs) -> None:
            self.connect_arguments = kwargs

        def open_sftp(self) -> FakeSftpClient:
            return sftp

        def close(self) -> None:
            self.closed = True

    class RejectPolicy:
        pass

    monkeypatch.setitem(
        sys.modules,
        "paramiko",
        SimpleNamespace(SSHClient=FakeSshClient, RejectPolicy=RejectPolicy),
    )
    monkeypatch.setenv("CAPTURE_NODE_SFTP_PASSWORD", "runtime-secret")
    known_hosts = tmp_path / "known_hosts"
    options = SftpStorageOptions(
        type="sftp",
        name="remote",
        host="storage.local",
        port=2022,
        username="capture-node",
        remote_directory="/captures",
        password_env="CAPTURE_NODE_SFTP_PASSWORD",
        known_hosts=known_hosts,
        connect_timeout_seconds=4,
        operation_timeout_seconds=9,
    )

    result = SftpStorage.from_options(options).save(b"capture", "one.jpg")

    [client] = clients
    assert result.succeeded
    assert client.host_keys == str(known_hosts)
    assert isinstance(client.policy, RejectPolicy)
    assert client.connect_arguments == {
        "hostname": "storage.local",
        "port": 2022,
        "username": "capture-node",
        "password": "runtime-secret",
        "key_filename": None,
        "passphrase": None,
        "timeout": 4.0,
        "banner_timeout": 4.0,
        "auth_timeout": 4.0,
        "channel_timeout": 9.0,
        "allow_agent": False,
        "look_for_keys": False,
    }
    assert sftp.channel.timeout == 9
    assert sftp.closed
    assert client.closed
