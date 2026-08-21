"""Lifecycle-owned adapter for the HUINTECHDEVICE CWT-Soil-THC-S driver."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from importlib import import_module
from types import ModuleType
from typing import Any, Callable, Protocol

from .adapters import AdapterError, PreviewByteStream
from .models import ActivityPlan, DataRecord, NodeDefinition, SensorReading


class SensorAdapterError(AdapterError):
    code = "sensor_error"


class SensorDependencyError(SensorAdapterError):
    code = "sensor_dependency_unavailable"


class SensorPortUnavailableError(SensorAdapterError):
    code = "sensor_port_unavailable"


class SensorTimeoutError(SensorAdapterError):
    code = "sensor_timeout"


class SensorCommunicationError(SensorAdapterError):
    code = "sensor_communication_error"


class SensorProtocolError(SensorAdapterError):
    code = "sensor_protocol_error"


class SensorModbusExceptionError(SensorProtocolError):
    code = "sensor_modbus_exception"


class CwtReadingProvider(Protocol):
    def health(self) -> dict[str, Any]: ...
    def read(self) -> SensorReading: ...
    def close(self) -> None: ...


class HuintechCwtProvider:
    """Own one serial stream and expose only temperature/humidity reads."""

    def __init__(
        self,
        definition: NodeDefinition,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        serial_module: ModuleType | Any | None = None,
        driver_module: ModuleType | Any | None = None,
    ) -> None:
        settings = definition.sensor_serial
        if settings is None or settings.driver != "cwt-soil-thc-s":
            raise ValueError(f"node {definition.id} has no CWT serial settings")
        self.definition = definition
        self.settings = settings
        self.clock = clock
        self._serial_module = serial_module
        self._driver_module = driver_module
        self._stream = None
        self._driver = None
        self._lock = threading.Lock()

    def _dependencies(self):
        if self._serial_module is None:
            try:
                self._serial_module = import_module("serial")
            except ImportError as exc:
                raise SensorDependencyError(
                    "pyserial is unavailable; install pi-node-supervisor[sensor]"
                ) from exc
        if self._driver_module is None:
            try:
                self._driver_module = import_module(
                    "huintech.sensors.soil.cwt_soil_thc_s"
                )
            except ImportError as exc:
                raise SensorDependencyError(
                    "HUINTECHDEVICE is unavailable; install its src package"
                ) from exc
        return self._serial_module, self._driver_module

    def _open(self) -> None:
        if self._driver is not None:
            return
        serial, driver = self._dependencies()
        stream = None
        try:
            stream = serial.Serial(
                self.settings.port,
                baudrate=self.settings.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.settings.timeout_seconds,
                write_timeout=self.settings.timeout_seconds,
            )
            stream.reset_input_buffer()
            sensor = driver.CWTSoilTHCS(stream, slave_id=self.settings.slave_id)
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise SensorPortUnavailableError(
                f"cannot open serial sensor port {self.settings.port}: {exc}"
            ) from exc
        self._stream = stream
        self._driver = sensor

    def health(self) -> dict[str, Any]:
        with self._lock:
            self._open()
            return {"serial_open": True}

    @staticmethod
    def _caused_by(error: BaseException, types: tuple[type[BaseException], ...]) -> bool:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            if isinstance(current, types):
                return True
            seen.add(id(current))
            current = current.__cause__ or current.__context__
        return False

    def read(self) -> SensorReading:
        with self._lock:
            try:
                self._open()
                assert self._driver is not None
                assert self._driver_module is not None
                fields = (
                    self._driver_module.CWTSoilTHCSMeasurement.HUMIDITY
                    | self._driver_module.CWTSoilTHCSMeasurement.TEMPERATURE
                )
                values = self._driver.read_measurements(fields)
                if values.temperature is None or values.humidity is None:
                    raise SensorProtocolError(
                        "CWT response omitted temperature or humidity"
                    )
                return SensorReading(
                    observed_at=self.clock(),
                    temperature_c=values.temperature,
                    humidity_percent=values.humidity,
                )
            except SensorAdapterError:
                self._close_unlocked()
                raise
            except Exception as exc:
                serial, driver = self._dependencies()
                timeout_types = tuple(
                    item
                    for item in (
                        TimeoutError,
                        getattr(serial, "SerialTimeoutException", None),
                    )
                    if isinstance(item, type)
                )
                if self._caused_by(exc, timeout_types):
                    mapped = SensorTimeoutError(f"CWT sensor timed out: {exc}")
                elif isinstance(exc, driver.ModbusExceptionResponseError):
                    mapped = SensorModbusExceptionError(str(exc))
                elif isinstance(exc, driver.ModbusProtocolError):
                    mapped = SensorProtocolError(str(exc))
                elif isinstance(exc, driver.ModbusCommunicationError):
                    mapped = SensorCommunicationError(str(exc))
                else:
                    mapped = SensorCommunicationError(
                        f"unexpected CWT communication failure: {exc}"
                    )
                self._close_unlocked()
                raise mapped from exc

    def _close_unlocked(self) -> None:
        stream, self._stream, self._driver = self._stream, None, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()


class CwtSensorAdapter:
    def __init__(self, definition: NodeDefinition, provider: CwtReadingProvider) -> None:
        self.definition = definition
        self.provider = provider
        self.node_id = definition.id

    def health(self) -> dict[str, Any]:
        settings = self.definition.sensor_serial
        assert settings is not None
        probe = getattr(self.provider, "health", None)
        provider_health = probe() if callable(probe) else {}
        return {
            "status": "configured",
            "driver": settings.driver,
            "port": settings.port,
            "slave_id": settings.slave_id,
            **provider_health,
        }

    def execute(self, activity: ActivityPlan, run_id: str) -> dict[str, Any]:
        if activity.activity_type != "sensor-collection":
            raise SensorAdapterError(
                f"CWT adapter cannot execute {activity.activity_type}"
            )
        reading = self.provider.read()
        return {
            **reading.model_dump(mode="json"),
            "scheduled_at": activity.requested_start.isoformat(),
            "run_id": run_id,
        }

    def preview_frame(self) -> tuple[bytes, str]:
        raise SensorAdapterError("sensor node does not provide a preview")

    def open_preview_stream(self) -> PreviewByteStream:
        raise SensorAdapterError("sensor node does not provide a preview stream")

    def list_data(
        self,
        *,
        activity_id: str | None = None,
        run_id: str | None = None,
        capture_id: str | None = None,
    ) -> list[DataRecord]:
        return []

    def close(self) -> None:
        self.provider.close()
