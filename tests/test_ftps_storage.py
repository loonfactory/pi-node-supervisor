from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from ftplib import error_perm
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError

import capture_node.core.storages.ftps_storage as ftps_module
from capture_node.core.storages import FtpsStorageOptions
from capture_node.core.storages.ftps_storage import FtpsStorage


@dataclass
class FakeRemoteFile:
    data: bytes
    modified: str = "20260803120000"


class FakeFtpsClient:
    def __init__(self, root: str = "/captures") -> None:
        self.root = root
        self.directories = {root}
        self.files: dict[str, FakeRemoteFile] = {}
        self.fail_rename = False
        self.mlsd_supported = True
        self.reported_size_adjustment = 0

    def cwd(self, dirname: str) -> str:
        if dirname not in self.directories:
            raise error_perm("550 directory unavailable")
        return "250 directory changed"

    def delete(self, filename: str) -> str:
        if filename not in self.files:
            raise error_perm("550 file unavailable")
        del self.files[filename]
        return "250 deleted"

    def mkd(self, pathname: str) -> str:
        if pathname in self.directories:
            raise error_perm("550 directory exists")
        self.directories.add(pathname)
        return pathname

    def mlsd(self, path: str = "", facts=None):
        if not self.mlsd_supported:
            raise error_perm("500 command not understood")
        prefix = f"{path.rstrip('/')}/"
        for remote_path, remote_file in self.files.items():
            if remote_path.startswith(prefix) and "/" not in remote_path[len(prefix) :]:
                yield remote_path.rsplit("/", 1)[-1], {
                    "type": "file",
                    "size": str(len(remote_file.data)),
                    "modify": remote_file.modified,
                }
        for remote_path in self.directories:
            if remote_path.startswith(prefix) and "/" not in remote_path[len(prefix) :]:
                yield remote_path.rsplit("/", 1)[-1], {"type": "dir"}

    def nlst(self, path: str) -> list[str]:
        prefix = f"{path.rstrip('/')}/"
        return [
            remote_path
            for remote_path in (*self.files, *self.directories)
            if remote_path.startswith(prefix)
            and "/" not in remote_path[len(prefix) :]
        ]

    def pwd(self) -> str:
        return self.root

    def sendcmd(self, cmd: str) -> str:
        path = cmd.removeprefix("MDTM ")
        if path not in self.files:
            raise error_perm("550 file unavailable")
        return f"213 {self.files[path].modified}"

    def size(self, filename: str) -> int:
        if filename not in self.files:
            raise error_perm("550 file unavailable")
        return len(self.files[filename].data) + self.reported_size_adjustment

    def rename(self, fromname: str, toname: str) -> str:
        if self.fail_rename:
            raise error_perm("550 rename failed")
        self.files[toname] = self.files.pop(fromname)
        return "250 renamed"

    def retrbinary(self, cmd: str, callback) -> str:
        path = cmd.removeprefix("RETR ")
        if path not in self.files:
            raise error_perm("550 file unavailable")
        callback(self.files[path].data)
        return "226 transfer complete"

    def storbinary(self, cmd: str, fp: BytesIO) -> str:
        path = cmd.removeprefix("STOR ")
        self.files[path] = FakeRemoteFile(fp.read())
        return "226 transfer complete"


def session_factory(client: FakeFtpsClient):
    @contextmanager
    def session():
        yield client

    return session


def test_ftps_storage_uses_daily_directories_and_reads_legacy_files() -> None:
    client = FakeFtpsClient()
    name = "20260803T131120.000000Z_capture.jpg"
    client.files["/captures/legacy.jpg"] = FakeRemoteFile(b"legacy")
    storage = FtpsStorage("remote", "/captures", session_factory(client))

    result = storage.save(b"daily", name)
    listing = storage.list(1, 10)

    assert result.succeeded
    assert f"/captures/20260803/{name}" in client.files
    assert storage.read(name) == b"daily"
    assert storage.read("legacy.jpg") == b"legacy"
    assert {item.name for item in listing.items} == {name, "legacy.jpg"}
    assert not any(path.endswith(".tmp") for path in client.files)

    storage.delete(name)

    assert f"/captures/20260803/{name}" not in client.files
    with pytest.raises(FileNotFoundError):
        storage.delete(name)


def test_ftps_storage_creates_missing_remote_path_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeFtpsClient(root="/")
    storage = FtpsStorage(
        "remote",
        "/archive/camera0",
        session_factory(client),
    )

    with caplog.at_level(logging.INFO, logger=ftps_module.__name__):
        first = storage.save(b"first", "20260804T120000.000000Z_first.jpg")
        second = storage.save(b"second", "20260804T121000.000000Z_second.jpg")

    assert first.succeeded
    assert second.succeeded
    assert "/archive" in client.directories
    assert "/archive/camera0" in client.directories
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == ftps_module.__name__
    ] == [
        "created FTPS remote directory: /archive",
        "created FTPS remote directory: /archive/camera0",
    ]


def test_ftps_storage_falls_back_when_mlsd_is_unsupported() -> None:
    client = FakeFtpsClient()
    client.mlsd_supported = False
    client.directories.add("/captures/20260803")
    name = "20260803T131120.000000Z_capture.jpg"
    client.files[f"/captures/20260803/{name}"] = FakeRemoteFile(b"daily")
    client.files["/captures/legacy.jpg"] = FakeRemoteFile(b"legacy")
    storage = FtpsStorage("remote", "/captures", session_factory(client))

    listing = storage.list(1, 10)

    assert {item.name for item in listing.items} == {name, "legacy.jpg"}


def test_ftps_storage_cleans_temporary_file_after_rename_failure() -> None:
    client = FakeFtpsClient()
    client.fail_rename = True
    storage = FtpsStorage("remote", "/captures", session_factory(client))

    result = storage.save(b"capture", "capture.jpg")

    assert not result.succeeded
    assert isinstance(result.cause, error_perm)
    assert client.files == {}


def test_ftps_storage_does_not_commit_an_incomplete_upload() -> None:
    client = FakeFtpsClient()
    client.reported_size_adjustment = -1
    storage = FtpsStorage("remote", "/captures", session_factory(client))

    result = storage.save(b"capture", "capture.jpg")

    assert not result.succeeded
    assert isinstance(result.cause, OSError)
    assert client.files == {}


def test_ftps_storage_rejects_escape_and_missing_files() -> None:
    storage = FtpsStorage(
        "remote",
        "/captures",
        session_factory(FakeFtpsClient()),
    )

    with pytest.raises(ValueError, match="filename"):
        storage.read("../secret")
    with pytest.raises(FileNotFoundError, match="does not exist"):
        storage.read("missing.jpg")


def test_ftps_options_require_an_environment_secret_and_valid_path(
    tmp_path: Path,
) -> None:
    options = FtpsStorageOptions(
        type="ftps",
        name="remote",
        host="localhost",
        username="capture-node",
        remote_directory="/captures",
        password_env="CAPTURE_NODE_FTPS_PASSWORD",
        ca_file=tmp_path / "ftps-ca.pem",
    )

    document = options.model_dump(mode="json")

    assert document["port"] == 21
    assert document["layout"] == "daily"
    assert document["verify_tls"] is True
    assert "password" not in document
    with pytest.raises(ValidationError, match="normalized absolute"):
        FtpsStorageOptions.model_validate(
            {**document, "remote_directory": "../captures"}
        )
    with pytest.raises(ValidationError, match="environment variable"):
        FtpsStorageOptions.model_validate({**document, "password_env": "bad-name"})
    with pytest.raises(ValidationError):
        FtpsStorageOptions.model_validate({**document, "password": "secret"})
    internal_options = FtpsStorageOptions.model_validate(
        {**document, "verify_tls": False}
    )
    assert internal_options.ca_file == tmp_path / "ftps-ca.pem"


def test_ftps_can_explicitly_disable_verification_for_an_isolated_network() -> None:
    options = FtpsStorageOptions(
        type="ftps",
        name="remote",
        host="192.168.50.1",
        username="capture-node",
        remote_directory="/captures",
        password_env="CAPTURE_NODE_FTPS_PASSWORD",
        verify_tls=False,
        ca_file=Path("unused-ca.pem"),
    )

    context = ftps_module._create_tls_context(options)

    assert context.verify_mode == ftps_module.ssl.CERT_NONE
    assert context.check_hostname is False


def test_ftps_session_verifies_tls_and_protects_the_data_channel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clients = []
    context = object()
    context_calls = []

    class FakeSocket:
        def __init__(self) -> None:
            self.timeout = None

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

    class FakeClient(FakeFtpsClient):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            self.constructor_arguments = kwargs
            self.connect_arguments = None
            self.login_arguments = None
            self.protected = False
            self.timeout = kwargs["timeout"]
            self.sock = FakeSocket()
            self.quit_called = False
            self.close_called = False
            clients.append(self)

        def connect(self, host: str, port: int, timeout: float) -> None:
            self.connect_arguments = (host, port, timeout)

        def login(self, username: str, password: str) -> None:
            self.login_arguments = (username, password)

        def prot_p(self) -> None:
            self.protected = True

        def quit(self) -> None:
            self.quit_called = True

        def close(self) -> None:
            self.close_called = True

    def create_default_context(*, cafile=None):
        context_calls.append(cafile)
        return context

    monkeypatch.setattr(ftps_module.ssl, "create_default_context", create_default_context)
    monkeypatch.setattr(ftps_module, "_CompatibleFTP_TLS", FakeClient)
    monkeypatch.setenv("CAPTURE_NODE_FTPS_PASSWORD", "runtime-secret")
    ca_file = tmp_path / "ftps-ca.pem"
    options = FtpsStorageOptions(
        type="ftps",
        name="remote",
        host="localhost",
        port=21,
        username="capture-node",
        remote_directory="/captures",
        password_env="CAPTURE_NODE_FTPS_PASSWORD",
        ca_file=ca_file,
        connect_timeout_seconds=4,
        operation_timeout_seconds=9,
    )

    result = FtpsStorage.from_options(options).save(b"capture", "one.jpg")

    [client] = clients
    assert result.succeeded
    assert context_calls == [str(ca_file)]
    assert client.constructor_arguments == {
        "context": context,
        "timeout": 4.0,
        "encoding": "utf-8",
    }
    assert client.connect_arguments == ("localhost", 21, 4.0)
    assert client.login_arguments == ("capture-node", "runtime-secret")
    assert client.protected
    assert client.timeout == 9.0
    assert client.sock.timeout == 9.0
    assert client.quit_called
    assert client.close_called


@pytest.mark.parametrize("failure", [TimeoutError(), ftps_module.ssl.SSLEOFError()])
def test_ftps_tolerates_missing_data_channel_close_notify(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    class FakeTlsSocket:
        def __init__(self) -> None:
            self.timeout = 30.0

        def gettimeout(self) -> float:
            return self.timeout

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def unwrap(self):
            raise failure

    monkeypatch.setattr(ftps_module.ssl, "SSLSocket", FakeTlsSocket)
    connection = FakeTlsSocket()

    ftps_module._finish_tls_data_channel(connection)

    assert connection.timeout == 1.0
