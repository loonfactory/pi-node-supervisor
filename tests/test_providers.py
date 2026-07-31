from __future__ import annotations

import io
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from capture_node.core.cameras import (
    CameraDevice,
    CameraRegistration,
    CameraRegistry,
    CameraDiscoveryProvider,
    CameraReplacementProvider,
    CameraStatusSource,
    Capture,
    ConfiguredCameraProvider,
    Picamera2Options,
    V4L2CameraOptions,
    default_camera_registry,
)
from capture_node.core.storages import (
    ConfiguredStorageProvider,
    FileStorageOptions,
    SftpStorageOptions,
    StorageRegistration,
    StorageRegistry,
    default_storage_registry,
)


class ProviderCamera:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False
        self.parameters: dict = {}

    def capture(self) -> Capture:
        return Capture(
            Image.new("RGB", (2, 2)),
            datetime.now(UTC),
            {"name": self.name},
        )

    def apply(self, parameters: Mapping) -> None:
        self.parameters = dict(parameters)

    def close(self) -> None:
        self.closed = True


class ProviderStorage:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def save(self, data: bytes, name: str):
        raise NotImplementedError

    def read(self, name: str) -> bytes:
        raise io.UnsupportedOperation

    def list(self, page: int, page_size: int):
        raise io.UnsupportedOperation


def camera_options(device: str) -> V4L2CameraOptions:
    return V4L2CameraOptions(type="v4l2", device=Path(device))


def camera_registry(factory, discoverer=None) -> CameraRegistry:
    return CameraRegistry(
        (
            CameraRegistration(
                "v4l2",
                V4L2CameraOptions,
                factory,
                discoverer,
            ),
        )
    )


def test_configured_camera_provider_owns_replacement_and_lifecycle() -> None:
    first = ProviderCamera("first")
    second = ProviderCamera("second")
    implementations = iter((first, second))
    provider = ConfiguredCameraProvider(
        camera_options("/dev/one"),
        registry=camera_registry(lambda _options: next(implementations)),
    )

    provider.replace(camera_options("/dev/two"), {"gain": 4})

    assert isinstance(provider, CameraReplacementProvider)
    assert first.closed
    assert provider.camera().capture().parameters["name"] == "second"
    assert second.parameters == {"gain": 4}
    provider.close()
    assert second.closed


def test_configured_camera_provider_restores_previous_options_on_failure() -> None:
    first = ProviderCamera("first")
    rollback = ProviderCamera("rollback")
    calls = 0

    def factory(options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        if options.device == Path("/dev/failing"):
            raise RuntimeError("new device failed")
        return rollback

    provider = ConfiguredCameraProvider(
        camera_options("/dev/one"),
        registry=camera_registry(factory),
    )

    with pytest.raises(RuntimeError, match="new device failed"):
        provider.replace(camera_options("/dev/failing"), {"gain": 2})

    assert first.closed
    assert provider.camera().capture().parameters["name"] == "rollback"
    assert rollback.parameters == {"gain": 2}


def test_configured_camera_provider_exposes_discovered_devices() -> None:
    usb = CameraDevice(
        identifier="v4l2:/dev/video4",
        name="USB Camera",
        options=camera_options("/dev/video4"),
    )
    provider = ConfiguredCameraProvider(
        camera_options("/dev/one"),
        registry=camera_registry(
            lambda _options: ProviderCamera("camera"),
            lambda: (usb,),
        ),
    )

    assert isinstance(provider, CameraDiscoveryProvider)
    assert [(item.identifier, item.name) for item in provider.devices()] == [
        ("v4l2:/dev/video4", "USB Camera")
    ]


def test_configured_camera_provider_can_expose_unavailable_camera(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-video-device"
    provider = ConfiguredCameraProvider(
        camera_options(str(missing)),
        tolerate_unavailable=True,
    )
    camera = provider.camera()

    assert isinstance(camera, CameraStatusSource)
    assert not camera.status().connected
    assert camera.status().detail == f"V4L2 device does not exist: {missing}"


def test_default_camera_registry_has_unique_supported_keys() -> None:
    assert default_camera_registry().keys == ("picamera2", "v4l2")

    duplicate = CameraRegistration(
        "v4l2",
        V4L2CameraOptions,
        lambda _options: ProviderCamera("duplicate"),
    )
    with pytest.raises(ValueError, match="already exists"):
        CameraRegistry((duplicate, duplicate))


def test_camera_registry_combines_discovery_from_every_registration() -> None:
    csi = CameraDevice(
        identifier="picamera2:1",
        name="imx219",
        options=Picamera2Options(camera_num=1),
    )
    usb = CameraDevice(
        identifier="v4l2:/dev/video4",
        name="USB Camera",
        options=camera_options("/dev/video4"),
    )
    registry = CameraRegistry(
        (
            CameraRegistration(
                "picamera2",
                Picamera2Options,
                lambda _options: ProviderCamera("csi"),
                lambda: (csi,),
            ),
            CameraRegistration(
                "v4l2",
                V4L2CameraOptions,
                lambda _options: ProviderCamera("usb"),
                lambda: (usb,),
            ),
        )
    )

    assert [device.identifier for device in registry.devices()] == [
        "picamera2:1",
        "v4l2:/dev/video4",
    ]


def test_configured_storage_provider_builds_registered_targets(tmp_path: Path) -> None:
    provider = ConfiguredStorageProvider(
        (
            FileStorageOptions(
                type="file",
                name="local",
                directory=tmp_path,
            ),
        )
    )

    [storage] = provider.storages()
    result = storage.save(b"capture", "one.bin")

    assert storage.name == "local"
    assert result.succeeded
    assert (tmp_path / "one.bin").read_bytes() == b"capture"


def test_default_storage_registry_has_unique_supported_keys() -> None:
    assert default_storage_registry().keys == ("file", "sftp")

    duplicate = StorageRegistration(
        "file",
        FileStorageOptions,
        lambda options: ProviderStorage(options.name),
    )
    with pytest.raises(ValueError, match="already exists"):
        StorageRegistry((duplicate, duplicate))


def test_configured_storage_provider_uses_injected_registration(
    tmp_path: Path,
) -> None:
    registration = StorageRegistration(
        "file",
        FileStorageOptions,
        lambda options: ProviderStorage(options.name),
    )
    provider = ConfiguredStorageProvider(
        (
            FileStorageOptions(
                type="file",
                name="injected",
                directory=tmp_path,
            ),
        ),
        registry=StorageRegistry((registration,)),
    )

    [storage] = provider.storages()

    assert isinstance(storage, ProviderStorage)
    assert storage.name == "injected"


def test_configured_storage_provider_builds_sftp_without_connecting() -> None:
    provider = ConfiguredStorageProvider(
        (
            SftpStorageOptions(
                type="sftp",
                name="archive",
                host="storage.local",
                username="capture-node",
                remote_directory="/captures",
                password_env="CAPTURE_NODE_SFTP_PASSWORD",
            ),
        )
    )

    [storage] = provider.storages()

    assert storage.name == "archive"
