"""Schema v2 source parsing and immutable runtime resolution."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import posixpath
import re
from datetime import time
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel


DeviceId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][A-Za-z0-9]*$",
    ),
]
_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SourceModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=False,
    )


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _wall_time(value: time) -> time:
    if value.tzinfo is not None:
        raise ValueError("wall-clock times must not contain an offset")
    return value


class IntervalScheduleSource(SourceModel):
    type: Literal["interval"]
    start_time: time = time(0, 0, 0)
    end_time: time = time(23, 59, 59)
    interval_seconds: int = Field(gt=0)

    _start_wall_time = field_validator("start_time")(_wall_time)
    _end_wall_time = field_validator("end_time")(_wall_time)


class DailyScheduleSource(SourceModel):
    type: Literal["daily"]
    time: time

    _daily_wall_time = field_validator("time")(_wall_time)


ScheduleSource = Annotated[
    IntervalScheduleSource | DailyScheduleSource,
    Field(discriminator="type"),
]


class LocalStorageSource(SourceModel):
    type: Literal["local"]
    base_path: str

    @field_validator("base_path")
    @classmethod
    def absolute_local_path(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or "\x00" in value:
            raise ValueError("local basePath must be a safe absolute path")
        return os.path.normpath(value)


class SftpStorageSource(SourceModel):
    type: Literal["sftp"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=100)
    base_path: str
    private_key_path: Path | None = None
    password_env: str | None = None
    private_key_passphrase_env: str | None = None
    known_hosts_path: Path | None = None
    connect_timeout_seconds: float = Field(10, gt=0, le=300)
    operation_timeout_seconds: float = Field(120, gt=0, le=3600)

    @field_validator("base_path")
    @classmethod
    def absolute_remote_path(cls, value: str) -> str:
        return _normalized_remote_base(value)

    @field_validator("password_env", "private_key_passphrase_env")
    @classmethod
    def environment_name(cls, value: str | None) -> str | None:
        if value is not None and not _ENVIRONMENT_VARIABLE.fullmatch(value):
            raise ValueError("secret environment variable name is invalid")
        return value

    @model_validator(mode="after")
    def authentication_source(self) -> SftpStorageSource:
        if self.private_key_path is None and self.password_env is None:
            raise ValueError("SFTP requires privateKeyPath or passwordEnv")
        if self.private_key_path is None and self.private_key_passphrase_env is not None:
            raise ValueError("privateKeyPassphraseEnv requires privateKeyPath")
        return self


class FtpsStorageSource(SourceModel):
    type: Literal["ftps"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(21, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=100)
    password_env: str
    base_path: str
    tls_mode: Literal["explicit"] = "explicit"
    verify_tls: bool = True
    ca_file_path: Path | None = None
    connect_timeout_seconds: float = Field(10, gt=0, le=300)
    operation_timeout_seconds: float = Field(120, gt=0, le=3600)

    @field_validator("base_path")
    @classmethod
    def absolute_remote_path(cls, value: str) -> str:
        return _normalized_remote_base(value)

    @field_validator("password_env")
    @classmethod
    def environment_name(cls, value: str) -> str:
        if not _ENVIRONMENT_VARIABLE.fullmatch(value):
            raise ValueError("secret environment variable name is invalid")
        return value


StorageSource = Annotated[
    LocalStorageSource | SftpStorageSource | FtpsStorageSource,
    Field(discriminator="type"),
]


class StorageReferenceSource(SourceModel):
    extends: str
    path: str | None = None


class CameraDefaultsSource(SourceModel):
    capture_node: dict[str, Any] = Field(default_factory=dict)
    schedule: DeviceId | None = None
    storages: tuple[StorageReferenceSource, ...] = ()


class CameraDeviceSource(SourceModel):
    capture_node: dict[str, Any] = Field(default_factory=dict)
    schedule: DeviceId | None = None
    storages: tuple[StorageReferenceSource, ...] | None = None


class CameraSynchronizationSource(SourceModel):
    server: DeviceId | None = None
    frame_rate: float = Field(5, gt=0, le=30)
    lead_seconds: float = Field(2, ge=0.5, le=10)
    max_skew_milliseconds: float = Field(10, gt=0, le=1000)


class CameraSectionSource(SourceModel):
    defaults: CameraDefaultsSource = Field(default_factory=CameraDefaultsSource)
    devices: dict[DeviceId, CameraDeviceSource] = Field(default_factory=dict)
    synchronization: CameraSynchronizationSource = Field(default_factory=CameraSynchronizationSource)


class SensorDriverSource(SourceModel):
    name: str | None = None
    port: str | None = None
    baud_rate: int | None = Field(None, gt=0)
    unit_id: int | None = Field(None, ge=1, le=247)
    timeout_seconds: float | None = Field(None, gt=0, le=300)


class SensorDefaultsSource(SourceModel):
    driver: SensorDriverSource | None = None
    schedule: DeviceId | None = None
    storages: tuple[StorageReferenceSource, ...] = ()

    @model_validator(mode="after")
    def no_device_address(self) -> SensorDefaultsSource:
        if self.driver is not None and (
            self.driver.port is not None or self.driver.unit_id is not None
        ):
            raise ValueError("sensor defaults cannot contain driver port or unitId")
        return self


class SensorDeviceSource(SourceModel):
    driver: SensorDriverSource
    schedule: DeviceId | None = None
    storages: tuple[StorageReferenceSource, ...] | None = None


class SensorSectionSource(SourceModel):
    defaults: SensorDefaultsSource = Field(default_factory=SensorDefaultsSource)
    devices: dict[DeviceId, SensorDeviceSource] = Field(default_factory=dict)


class PoliciesSource(SourceModel):
    clock_tolerance_seconds: int = Field(5, ge=0)


class PowerWindowSource(SourceModel):
    start_time: time
    end_time: time

    _start_wall_time = field_validator("start_time")(_wall_time)
    _end_wall_time = field_validator("end_time")(_wall_time)


class PowerSource(SourceModel):
    boot_lead_seconds: int = Field(45, ge=0)
    shutdown_grace_seconds: int = Field(30, ge=0)
    min_off_seconds: int = Field(300, ge=60)
    windows: tuple[PowerWindowSource, ...] = ()


class SchemaV2Configuration(SourceModel):
    schema_version: Literal[2]
    policies: PoliciesSource = Field(default_factory=PoliciesSource)
    schedules: dict[DeviceId, tuple[ScheduleSource, ...]] = Field(default_factory=dict)
    storages: dict[DeviceId, StorageSource] = Field(default_factory=dict)
    cameras: CameraSectionSource = Field(default_factory=CameraSectionSource)
    sensors: SensorSectionSource = Field(default_factory=SensorSectionSource)
    power: PowerSource = Field(default_factory=PowerSource)


class ResolvedStorage(RuntimeModel):
    name: str
    type: Literal["local", "sftp", "ftps"]
    base_path: str
    path: str
    final_path: str
    settings: dict[str, Any]


class ResolvedDriver(RuntimeModel):
    name: str
    port: str
    baud_rate: int
    unit_id: int
    timeout_seconds: float


class ResolvedCamera(RuntimeModel):
    device_id: str
    capture_node: dict[str, Any]
    schedule_name: str | None
    schedule: tuple[ScheduleSource, ...]
    storages: tuple[ResolvedStorage, ...]


class ResolvedSensor(RuntimeModel):
    device_id: str
    driver: ResolvedDriver
    schedule_name: str | None
    schedule: tuple[ScheduleSource, ...]
    storages: tuple[ResolvedStorage, ...]


class ResolvedPower(RuntimeModel):
    boot_lead_seconds: int
    shutdown_grace_seconds: int
    min_off_seconds: int
    windows: tuple[PowerWindowSource, ...]


class ResolvedConfiguration(RuntimeModel):
    schema_version: Literal[2] = 2
    source_hash: str
    clock_tolerance_seconds: int
    cameras: dict[str, ResolvedCamera]
    sensors: dict[str, ResolvedSensor]
    power: ResolvedPower
    warnings: tuple[str, ...] = ()
    synchronization: CameraSynchronizationSource | None = None


class Resolution(RuntimeModel):
    source: SchemaV2Configuration
    resolved: ResolvedConfiguration
    capture_node_configs: dict[str, dict[str, Any]]


def resolve_configuration(document: dict[str, Any]) -> Resolution:
    source = SchemaV2Configuration.model_validate(document)
    duplicate_devices = set(source.cameras.devices).intersection(source.sensors.devices)
    if duplicate_devices:
        raise ValueError(
            f"device id is used by both camera and sensor: {sorted(duplicate_devices)[0]}"
        )
    schedule_names = set(source.schedules)
    storage_names = set(source.storages)
    referenced_schedules: set[str] = set()
    referenced_storages: set[str] = set()

    cameras: dict[str, ResolvedCamera] = {}
    generated: dict[str, dict[str, Any]] = {}
    camera_defaults = source.cameras.defaults.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    if camera_defaults.get("captureNode", {}).get("camera", {}).get("calibration"):
        raise ValueError("camera calibration must be configured per device, not in defaults")
    synchronization = None
    if len(source.cameras.devices) >= 2:
        synchronization = source.cameras.synchronization.model_copy(update={
            "server": source.cameras.synchronization.server or min(source.cameras.devices)
        })
    for device_id, device in source.cameras.devices.items():
        device_document = device.model_dump(mode="json", by_alias=True, exclude_none=True)
        merged = _deep_merge(camera_defaults, device_document)
        schedule_name = merged.get("schedule")
        schedule = _resolve_schedule(schedule_name, source.schedules, referenced_schedules)
        storage_references = merged.get("storages", [])
        resolved_storages = _resolve_storages(
            device_id, storage_references, source, referenced_storages
        )
        capture_node = copy.deepcopy(merged.get("captureNode", {}))
        if synchronization is not None:
            camera = capture_node.setdefault("camera", {})
            if camera.get("type", "picamera2") != "picamera2":
                raise ValueError("camera synchronization requires picamera2 devices")
            reserved = {"FrameRate", "FrameDurationLimits", "SyncMode", "SyncFrames"}
            if reserved.intersection(camera.get("params", {})) or camera.get("synchronization"):
                raise ValueError("camera timing must be configured through cameras.synchronization")
            camera["synchronization"] = {
                "role": "server" if device_id == synchronization.server else "client",
                "frameRate": synchronization.frame_rate,
            }
        generated_document = {
            key: value
            for key, value in capture_node.items()
            if key not in {"captures", "storages"}
        }
        cameras[device_id] = ResolvedCamera(
            device_id=device_id,
            capture_node=capture_node,
            schedule_name=schedule_name,
            schedule=schedule,
            storages=resolved_storages,
        )
        generated[device_id] = generated_document

    if synchronization is not None:
        if synchronization.server not in cameras:
            raise ValueError("camera synchronization requires at least two cameras and a valid server")
        schedules = [camera.schedule for camera in cameras.values()]
        if any(schedule != schedules[0] for schedule in schedules):
            raise ValueError("synchronized cameras must use the same schedule")
        numbers = [camera.capture_node["camera"].get("cameraNum", 0) for camera in cameras.values()]
        if len(set(numbers)) != len(numbers):
            raise ValueError("synchronized cameras must use distinct cameraNum values")

    sensors: dict[str, ResolvedSensor] = {}
    sensor_defaults = source.sensors.defaults.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    bus_addresses: set[tuple[str, int]] = set()
    for device_id, device in source.sensors.devices.items():
        device_document = device.model_dump(mode="json", by_alias=True, exclude_none=True)
        merged = _deep_merge(sensor_defaults, device_document)
        schedule_name = merged.get("schedule")
        schedule = _resolve_schedule(schedule_name, source.schedules, referenced_schedules)
        resolved_storages = _resolve_storages(
            device_id, merged.get("storages", []), source, referenced_storages
        )
        driver = _resolve_driver(device_id, merged.get("driver", {}))
        address = (driver.port, driver.unit_id)
        if address in bus_addresses:
            raise ValueError(
                f"duplicate sensor unitId {driver.unit_id} on port {driver.port}"
            )
        bus_addresses.add(address)
        sensors[device_id] = ResolvedSensor(
            device_id=device_id,
            driver=driver,
            schedule_name=schedule_name,
            schedule=schedule,
            storages=resolved_storages,
        )

    warnings = tuple(
        [f"unused schedule: {name}" for name in sorted(schedule_names - referenced_schedules)]
        + [f"unused storage: {name}" for name in sorted(storage_names - referenced_storages)]
    )
    canonical = json.dumps(
        source.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    resolved = ResolvedConfiguration(
        source_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        clock_tolerance_seconds=source.policies.clock_tolerance_seconds,
        cameras=cameras,
        sensors=sensors,
        power=ResolvedPower(
            boot_lead_seconds=source.power.boot_lead_seconds,
            shutdown_grace_seconds=source.power.shutdown_grace_seconds,
            min_off_seconds=source.power.min_off_seconds,
            windows=source.power.windows,
        ),
        warnings=warnings,
        synchronization=synchronization,
    )
    return Resolution(source=source, resolved=resolved, capture_node_configs=generated)


def _resolve_schedule(
    name: str | None,
    schedules: dict[str, tuple[ScheduleSource, ...]],
    referenced: set[str],
) -> tuple[ScheduleSource, ...]:
    if name is None:
        return ()
    try:
        result = schedules[name]
    except KeyError as exc:
        raise ValueError(f"schedule does not exist: {name}") from exc
    referenced.add(name)
    return result


def _resolve_storages(
    device_id: str,
    references: list[dict[str, Any]],
    source: SchemaV2Configuration,
    referenced: set[str],
) -> tuple[ResolvedStorage, ...]:
    result: list[ResolvedStorage] = []
    for reference in references:
        pointer = reference["extends"]
        storage_name = _storage_pointer_name(pointer)
        try:
            storage = source.storages[storage_name]
        except KeyError as exc:
            raise ValueError(f"storage does not exist: {storage_name}") from exc
        path = _relative_storage_path(reference.get("path", device_id))
        storage_document = storage.model_dump(mode="json", by_alias=True, exclude_none=True)
        base_path = storage_document["basePath"]
        final_path = (
            os.path.join(base_path, path)
            if storage.type == "local"
            else posixpath.join(base_path, path)
        )
        referenced.add(storage_name)
        result.append(
            ResolvedStorage(
                name=storage_name,
                type=storage.type,
                base_path=base_path,
                path=path,
                final_path=final_path,
                settings=storage_document,
            )
        )
    return tuple(result)


def _resolve_driver(device_id: str, value: dict[str, Any]) -> ResolvedDriver:
    required = ("name", "port", "unitId")
    missing = [key for key in required if value.get(key) is None]
    if missing:
        raise ValueError(f"sensor {device_id} driver is missing {', '.join(missing)}")
    port = value["port"]
    if not isinstance(port, str) or not port.startswith("/dev/") or ".." in Path(port).parts:
        raise ValueError(f"sensor {device_id} driver port must be a safe /dev path")
    return ResolvedDriver(
        name=value["name"],
        port=port,
        baud_rate=value.get("baudRate", 4800),
        unit_id=value["unitId"],
        timeout_seconds=value.get("timeoutSeconds", 1.0),
    )


def _storage_pointer_name(pointer: str) -> str:
    prefix = "#/storages/"
    if not pointer.startswith(prefix):
        raise ValueError(f"storage extends must target #/storages/*: {pointer}")
    encoded = pointer[len(prefix) :]
    if not encoded or "/" in encoded:
        raise ValueError(f"invalid storage JSON Pointer: {pointer}")
    name = encoded.replace("~1", "/").replace("~0", "~")
    if "~" in re.sub(r"~[01]", "", encoded):
        raise ValueError(f"invalid JSON Pointer escape: {pointer}")
    return name


def _relative_storage_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("storage path must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError(f"storage path must be normalized and relative: {value}")
    return value


def _normalized_remote_base(value: str) -> str:
    if (
        not value.startswith("/")
        or "\x00" in value
        or posixpath.normpath(value) != value
        or ".." in PurePosixPath(value).parts
    ):
        raise ValueError("remote basePath must be a normalized absolute POSIX path")
    return value


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = copy.deepcopy(value)
    return result
