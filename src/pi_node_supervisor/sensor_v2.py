"""V2 CWT soil sensor provider built directly from resolved configuration."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Callable

from .config_v2 import ResolvedDriver
from .sensor_models import SensorReading


class CwtSensorProviderV2:
    def __init__(
        self,
        settings: ResolvedDriver,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        serial_module: Any | None = None,
        driver_module: Any | None = None,
    ) -> None:
        if settings.name != "cwt_soil_thc_s":
            raise ValueError(f"unsupported sensor driver: {settings.name}")
        self.settings = settings
        self.clock = clock
        self.serial_module = serial_module
        self.driver_module = driver_module
        self._stream = None
        self._driver = None
        self._lock = threading.Lock()

    def _dependencies(self):
        if self.serial_module is None:
            self.serial_module = import_module("serial")
        if self.driver_module is None:
            self.driver_module = import_module("huintech.sensors.soil.cwt_soil_thc_s")
        return self.serial_module, self.driver_module

    def _open(self) -> None:
        if self._driver is not None:
            return
        serial, module = self._dependencies()
        stream = serial.Serial(
            self.settings.port,
            baudrate=self.settings.baud_rate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.settings.timeout_seconds,
            write_timeout=self.settings.timeout_seconds,
        )
        try:
            stream.reset_input_buffer()
            driver = module.CWTSoilTHCS(stream, slave_id=self.settings.unit_id)
        except BaseException:
            stream.close()
            raise
        self._stream = stream
        self._driver = driver

    def read(self) -> SensorReading:
        with self._lock:
            try:
                self._open()
                assert self._driver is not None
                assert self.driver_module is not None
                fields = (
                    self.driver_module.CWTSoilTHCSMeasurement.HUMIDITY
                    | self.driver_module.CWTSoilTHCSMeasurement.TEMPERATURE
                )
                values = self._driver.read_measurements(fields)
                if values.temperature is None or values.humidity is None:
                    raise RuntimeError("CWT response omitted temperature or humidity")
                return SensorReading(
                    observed_at=self.clock(),
                    temperature_c=values.temperature,
                    humidity_percent=values.humidity,
                )
            except BaseException:
                self._close_unlocked()
                raise

    def _close_unlocked(self) -> None:
        stream, self._stream, self._driver = self._stream, None, None
        if stream is not None:
            stream.close()

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()
