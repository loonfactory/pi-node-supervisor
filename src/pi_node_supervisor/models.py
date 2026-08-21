"""Strict public configuration and derived-plan models."""

from __future__ import annotations

from datetime import UTC, datetime, time as Time
from ipaddress import ip_address
import math
import posixpath
import re
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .timeutil import validate_timezone

Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class SensorReading(StrictModel):
    observed_at: datetime
    temperature_c: float
    humidity_percent: float = Field(ge=0, le=100)

    @field_validator("temperature_c", "humidity_percent")
    @classmethod
    def finite_measurement(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("sensor measurements must be finite")
        return value

    @field_validator("observed_at")
    @classmethod
    def aware_observation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a UTC offset")
        return value.astimezone(UTC)


class Policies(StrictModel):
    boot_lead_seconds: int = Field(120, ge=0, alias="boot-lead-seconds")
    shutdown_grace_seconds: int = Field(60, ge=0, alias="shutdown-grace-seconds")
    merge_power_gaps_shorter_than_seconds: int = Field(
        300, ge=0, alias="merge-power-gaps-shorter-than-seconds"
    )
    minimum_lead_seconds: int = Field(60, ge=1, alias="minimum-lead-seconds")
    clock_tolerance_seconds: int = Field(5, ge=0, alias="clock-tolerance-seconds")
    recurrence_horizon_hours: int = Field(
        168, ge=24, le=744, alias="recurrence-horizon-hours"
    )


class TimedActivity(StrictModel):
    id: Identifier
    node: Identifier
    profile: Identifier
    at: str
    duration_seconds: int | None = Field(None, gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)


class CaptureActivity(TimedActivity):
    type: Literal["capture"]


class SensorActivity(TimedActivity):
    type: Literal["sensor-collection"]


class MaintenanceActivity(StrictModel):
    id: Identifier
    type: Literal["maintenance-window"]
    start: str
    end: str
    node: Identifier | None = None
    profile: Identifier | None = None


Activity = Annotated[
    CaptureActivity | SensorActivity | MaintenanceActivity,
    Field(discriminator="type"),
]
ACTIVITY_ADAPTER = TypeAdapter(Activity)


class ManagedCaptureProcess(StrictModel):
    """Restricted CaptureNode child-process definition.

    The executable is a Supervisor-wide deployment option rather than node
    configuration, so registry documents cannot introduce arbitrary commands.
    """

    config_path: Path
    working_directory: Path | None = None
    host: str = "127.0.0.1"
    port: int = Field(ge=1, le=65535)
    startup_timeout_seconds: float = Field(30.0, gt=0, le=300)
    restart_delay_seconds: float = Field(2.0, ge=0.1, le=60)

    @field_validator("config_path")
    @classmethod
    def absolute_config_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("managed CaptureNode config_path must be absolute")
        return value

    @field_validator("working_directory")
    @classmethod
    def absolute_working_directory(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("managed CaptureNode working_directory must be absolute")
        return value

    @field_validator("host")
    @classmethod
    def loopback_host(cls, value: str) -> str:
        try:
            address = ip_address(value)
        except ValueError as exc:
            raise ValueError("managed CaptureNode host must be an IP address") from exc
        if not address.is_loopback:
            raise ValueError("managed CaptureNode must bind to a loopback address")
        return str(address)


class FileStorageSettings(StrictModel):
    type: Literal["file"]
    name: Identifier
    directory: Path
    layout: Literal["flat", "daily"] = "daily"

    @field_validator("directory")
    @classmethod
    def safe_directory(cls, value: Path) -> Path:
        if ".." in value.parts or "\x00" in str(value):
            raise ValueError("storage directory must not traverse parent directories")
        return value


class SftpStorageSettings(StrictModel):
    type: Literal["sftp"]
    name: Identifier
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=100)
    remote_directory: str
    private_key: Path | None = None
    password_env: str | None = None
    private_key_passphrase_env: str | None = None
    known_hosts: Path = Path("~/.ssh/known_hosts")
    connect_timeout_seconds: float = Field(10, gt=0, le=300)
    operation_timeout_seconds: float = Field(30, gt=0, le=600)
    layout: Literal["flat", "daily"] = "daily"

    @field_validator("remote_directory")
    @classmethod
    def normalized_remote_directory(cls, value: str) -> str:
        if not value.startswith("/") or "\x00" in value or posixpath.normpath(value) != value:
            raise ValueError("remote_directory must be a normalized absolute POSIX path")
        return value

    @field_validator("password_env", "private_key_passphrase_env")
    @classmethod
    def valid_environment_name(cls, value: str | None) -> str | None:
        if value is not None and not _ENVIRONMENT_VARIABLE.fullmatch(value):
            raise ValueError("secret environment variable name is invalid")
        return value

    @model_validator(mode="after")
    def authentication_reference(self) -> "SftpStorageSettings":
        if self.private_key is None and self.password_env is None:
            raise ValueError("SFTP requires a private key or password environment variable")
        if self.private_key is None and self.private_key_passphrase_env is not None:
            raise ValueError("a private-key passphrase requires a private key")
        return self


class FtpsStorageSettings(StrictModel):
    type: Literal["ftps"]
    name: Identifier
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(21, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=100)
    remote_directory: str
    password_env: str
    verify_tls: bool = True
    ca_file: Path | None = None
    connect_timeout_seconds: float = Field(10, gt=0, le=300)
    operation_timeout_seconds: float = Field(30, gt=0, le=600)
    layout: Literal["flat", "daily"] = "daily"

    @field_validator("remote_directory")
    @classmethod
    def normalized_remote_directory(cls, value: str) -> str:
        if not value.startswith("/") or "\x00" in value or posixpath.normpath(value) != value:
            raise ValueError("remote_directory must be a normalized absolute POSIX path")
        return value

    @field_validator("password_env")
    @classmethod
    def valid_environment_name(cls, value: str) -> str:
        if not _ENVIRONMENT_VARIABLE.fullmatch(value):
            raise ValueError("secret environment variable name is invalid")
        return value


StorageSettings = Annotated[
    FileStorageSettings | SftpStorageSettings | FtpsStorageSettings,
    Field(discriminator="type"),
]


class StorageRetentionSettings(StrictModel):
    target: Identifier
    max_age_days: int = Field(gt=0, le=36500)


class StorageSettingsDocument(StrictModel):
    items: tuple[StorageSettings, ...]
    retention: tuple[StorageRetentionSettings, ...] | None = None

    @model_validator(mode="after")
    def unique_names(self) -> "StorageSettingsDocument":
        names = [item.name for item in self.items]
        if len(names) != len(set(names)):
            raise ValueError("storage names must be unique")
        if self.retention is not None:
            targets = [item.target for item in self.retention]
            if len(targets) != len(set(targets)):
                raise ValueError("retention targets must be unique")
            missing = set(targets).difference(names)
            if missing:
                raise ValueError(
                    f"retention target is not configured: {sorted(missing)[0]}"
                )
        return self


class SensorStorageSettings(StrictModel):
    local_directory: Path
    mirrors: tuple[StorageSettings, ...] = ()
    retry_base_seconds: int = Field(30, ge=1, le=3600)
    retry_max_seconds: int = Field(3600, ge=1, le=86400)

    @field_validator("local_directory")
    @classmethod
    def safe_local_directory(cls, value: Path) -> Path:
        if ".." in value.parts or "\x00" in str(value):
            raise ValueError("sensor local directory must not traverse parent directories")
        return value

    @model_validator(mode="after")
    def valid_mirrors(self) -> "SensorStorageSettings":
        names = [item.name for item in self.mirrors]
        if len(names) != len(set(names)):
            raise ValueError("sensor mirror names must be unique")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry_max_seconds must be at least retry_base_seconds")
        return self


class CaptureScheduleSettings(StrictModel):
    """CaptureNode-native automatic capture schedule."""

    mode: Literal["interval", "daily", "disabled"]
    interval_seconds: float | None = Field(None, gt=0)
    daily_times: tuple[Time, ...] = ()
    timezone: str = "Asia/Seoul"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return validate_timezone(value)

    @model_validator(mode="after")
    def valid_schedule(self) -> "CaptureScheduleSettings":
        if len(self.daily_times) != len(set(self.daily_times)):
            raise ValueError("daily capture times must be unique")
        if any(value.tzinfo is not None for value in self.daily_times):
            raise ValueError("daily capture times must not include an offset")
        if self.mode == "interval":
            if self.interval_seconds is None:
                raise ValueError("interval_seconds is required in interval mode")
            if self.daily_times:
                raise ValueError("daily_times must be empty in interval mode")
        elif self.mode == "daily":
            if self.interval_seconds is not None:
                raise ValueError("interval_seconds must be null in daily mode")
        elif self.interval_seconds is not None or self.daily_times:
            raise ValueError("disabled mode cannot contain a capture schedule")
        return self


class CaptureScheduleDefinition(CaptureScheduleSettings):
    """Power-linked recurring capture owned by the Supervisor."""

    id: Identifier
    node: Identifier
    profile: Identifier = "default"
    anchor_at: str | None = None
    duration_seconds: int | None = Field(None, gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_anchor(self) -> "CaptureScheduleDefinition":
        if self.mode == "interval" and self.anchor_at is None:
            raise ValueError("anchor_at is required in interval mode")
        if self.mode != "interval" and self.anchor_at is not None:
            raise ValueError("anchor_at is only valid in interval mode")
        return self


class CaptureScheduleUpdate(StrictModel):
    target: Identifier
    settings: CaptureScheduleSettings


class SensorScheduleSettings(StrictModel):
    """Supervisor-owned daily wall-clock sensor collection schedule."""

    mode: Literal["daily", "disabled"]
    daily_times: tuple[Time, ...] = ()
    timezone: str = "Asia/Seoul"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return validate_timezone(value)

    @model_validator(mode="after")
    def valid_schedule(self) -> "SensorScheduleSettings":
        if len(self.daily_times) != len(set(self.daily_times)):
            raise ValueError("daily sensor times must be unique")
        if any(value.tzinfo is not None for value in self.daily_times):
            raise ValueError("daily sensor times must not include an offset")
        if self.mode == "disabled" and self.daily_times:
            raise ValueError("disabled mode cannot contain a sensor schedule")
        return self


class SensorScheduleDefinition(SensorScheduleSettings):
    """Recurring sensor collection expanded and executed by the Supervisor."""

    id: Identifier
    node: Identifier
    profile: Identifier = "default"
    duration_seconds: int | None = Field(None, gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)


class SensorScheduleUpdate(StrictModel):
    target: Identifier
    settings: SensorScheduleSettings


class DailyPowerKeepSchedule(StrictModel):
    """Daily wall-clock window that keeps the device powered independently."""

    id: Identifier
    start: Time
    end: Time
    timezone: str = "Asia/Seoul"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return validate_timezone(value)

    @model_validator(mode="after")
    def valid_window(self) -> "DailyPowerKeepSchedule":
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("power keep times must not include an offset")
        if self.end <= self.start:
            raise ValueError("power keep end must be later than start")
        return self


class PowerSettingsUpdate(StrictModel):
    warmup_seconds: int = Field(ge=0, le=86400)
    cleanup_seconds: int = Field(ge=0, le=86400)
    keep_schedules: tuple[DailyPowerKeepSchedule, ...] = ()

    @model_validator(mode="after")
    def unique_keep_ids(self) -> "PowerSettingsUpdate":
        ids = [item.id for item in self.keep_schedules]
        if len(ids) != len(set(ids)):
            raise ValueError("power keep schedule ids must be unique")
        return self


class CaptureNodeSettings(StrictModel):
    """CaptureNode-native JSON overlays stored with a Supervisor revision."""

    common: dict[str, Any] = Field(default_factory=dict)
    nodes: dict[Identifier, dict[str, Any]] = Field(default_factory=dict)


class SensorSerialSettings(StrictModel):
    driver: Literal["cwt-soil-thc-s"] = "cwt-soil-thc-s"
    port: str = Field(min_length=6, max_length=255)
    baudrate: int = 4800
    timeout_seconds: float = Field(1.0, gt=0, le=30)
    slave_id: int = Field(1, ge=1, le=254)

    @field_validator("port")
    @classmethod
    def safe_serial_port(cls, value: str) -> str:
        allowed = re.compile(
            r"^/dev/(?:ttyAMA\d+|ttyUSB\d+|ttyACM\d+|"
            r"serial/by-id/[A-Za-z0-9._:+-]+)$"
        )
        if not allowed.fullmatch(value) or "\x00" in value or ".." in Path(value).parts:
            raise ValueError("serial port must be an allowed absolute /dev path")
        return value

    @field_validator("baudrate")
    @classmethod
    def supported_baudrate(cls, value: int) -> int:
        if value not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}:
            raise ValueError("unsupported serial baudrate")
        return value


class SensorNodeSettingsUpdate(StrictModel):
    sensor_serial: SensorSerialSettings
    sensor_storage: SensorStorageSettings


class SupervisorConfiguration(StrictModel):
    schema_version: Literal[1] = 1
    timezone: str
    policies: Policies = Field(default_factory=Policies)
    activities: tuple[Activity, ...] = ()
    capture_schedules: tuple[CaptureScheduleDefinition, ...] = ()
    sensor_schedules: tuple[SensorScheduleDefinition, ...] = ()
    power_keep_schedules: tuple[DailyPowerKeepSchedule, ...] = ()
    capture_node_settings: CaptureNodeSettings = Field(default_factory=CaptureNodeSettings)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return validate_timezone(value)

    @model_validator(mode="after")
    def unique_activity_ids(self) -> "SupervisorConfiguration":
        ids = [activity.id for activity in self.activities] + [
            schedule.id for schedule in self.capture_schedules
        ] + [schedule.id for schedule in self.sensor_schedules] + [
            schedule.id for schedule in self.power_keep_schedules
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("activity ids must be unique across activities and schedules")
        capture_nodes = [schedule.node for schedule in self.capture_schedules]
        if len(capture_nodes) != len(set(capture_nodes)):
            raise ValueError("each node can have only one capture schedule")
        sensor_nodes = [schedule.node for schedule in self.sensor_schedules]
        if len(sensor_nodes) != len(set(sensor_nodes)):
            raise ValueError("each node can have only one sensor schedule")
        return self


class NodeDefinition(StrictModel):
    id: Identifier
    kind: Identifier
    activity_types: tuple[Literal["capture", "sensor-collection", "maintenance-window"], ...]
    profiles: tuple[Identifier, ...] = ()
    boot_ready_seconds: int = Field(0, ge=0)
    prepare_seconds: int = Field(0, ge=0)
    default_runtime_seconds: int = Field(1, gt=0)
    cleanup_seconds: int = Field(0, ge=0)
    required: bool = True
    endpoint: str | None = None
    request_timeout_seconds: float = Field(10.0, gt=0, le=300)
    completion_timeout_seconds: float = Field(60.0, gt=0, le=3600)
    managed_capture: ManagedCaptureProcess | None = None
    sensor_serial: SensorSerialSettings | None = None
    sensor_storage: SensorStorageSettings | None = None

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("node endpoint must use http:// or https://")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("node endpoint must not contain credentials, query, or fragment")
        if any(character in normalized for character in {'"', "'", "<", ">", "\r", "\n"}):
            raise ValueError("node endpoint contains unsafe characters")
        return normalized

    @model_validator(mode="after")
    def unique_capabilities(self) -> "NodeDefinition":
        if len(self.activity_types) != len(set(self.activity_types)):
            raise ValueError(f"node {self.id} activity_types must be unique")
        if len(self.profiles) != len(set(self.profiles)):
            raise ValueError(f"node {self.id} profiles must be unique")
        if self.managed_capture is not None:
            if self.kind != "capture-node":
                raise ValueError("managed_capture is only valid for capture-node")
            if self.endpoint is None:
                raise ValueError("managed CaptureNode requires an endpoint")
            expected = f"http://{self.managed_capture.host}:{self.managed_capture.port}"
            if self.endpoint != expected:
                raise ValueError(
                    f"managed CaptureNode endpoint must match {expected}"
                )
        if self.sensor_serial is not None:
            if self.kind != "sensor-node":
                raise ValueError("sensor_serial is only valid for sensor-node")
            if "sensor-collection" not in self.activity_types:
                raise ValueError("sensor_serial requires sensor-collection capability")
            if "temperature-humidity" not in self.profiles:
                raise ValueError("CWT sensor requires temperature-humidity profile")
            if self.endpoint is not None:
                raise ValueError("serial sensor-node cannot also configure an HTTP endpoint")
        if self.sensor_storage is not None:
            if self.kind != "sensor-node":
                raise ValueError("sensor_storage is only valid for sensor-node")
            if "sensor-collection" not in self.activity_types:
                raise ValueError("sensor_storage requires sensor-collection capability")
        return self


class NodeRegistryDocument(StrictModel):
    schema_version: Literal[1] = 1
    nodes: tuple[NodeDefinition, ...]

    @model_validator(mode="after")
    def unique_node_ids(self) -> "NodeRegistryDocument":
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        return self


class ActivityPlan(StrictModel):
    activity_id: Identifier
    activity_type: str
    node_id: Identifier
    profile: Identifier | None
    parameters: dict[str, Any] = Field(default_factory=dict)
    requested_start: datetime
    requested_end: datetime
    prepare_at: datetime
    complete_at: datetime
    release_at: datetime
    power_on: datetime
    power_down: datetime
    reasons: tuple[str, ...]


class PowerWindowPlan(StrictModel):
    power_on: datetime
    power_down: datetime
    activity_ids: tuple[Identifier, ...]
    next_power_on: datetime | None
    automatic_shutdown_possible: bool
    reasons: tuple[str, ...]


class DerivedPlan(StrictModel):
    schema_version: Literal[1] = 1
    timezone: str
    source_hash: str
    activities: tuple[ActivityPlan, ...]
    power_windows: tuple[PowerWindowPlan, ...]


class PowerManagerWindow(StrictModel):
    power_on: str = Field(alias="power-on")
    power_down: str = Field(alias="power-down")


class PowerManagerConfiguration(StrictModel):
    schema_version: Literal[2] = 2
    timezone: str
    minimum_lead_seconds: int = Field(ge=1)
    clock_tolerance_seconds: int = Field(ge=0)
    power_schedule: tuple[PowerManagerWindow, ...]


class RunRecord(StrictModel):
    run_id: str
    revision_id: int
    activity_id: str
    node_id: str
    status: Literal["running", "succeeded", "failed", "missed", "interrupted"]
    scheduled_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    external_id: str | None
    result: dict[str, Any] | None
    error: str | None


class EventRecord(StrictModel):
    id: int
    occurred_at: datetime
    event_type: str
    severity: Literal["info", "warning", "error"]
    run_id: str | None
    activity_id: str | None
    node_id: str | None
    payload: dict[str, Any]


class DataRecord(StrictModel):
    data_id: str
    node_id: str
    activity_id: str | None
    run_id: str | None
    kind: str
    captured_at: datetime
    uri: str
    media_type: str | None
    size: int | None
    integrity: str
    summary: str
    indexed_at: datetime
