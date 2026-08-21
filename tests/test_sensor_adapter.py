from __future__ import annotations

from datetime import UTC, datetime
from enum import IntFlag
from types import SimpleNamespace

import pytest

from conftest import configuration
from pi_node_supervisor.adapters import build_adapters
from pi_node_supervisor.executor import ExecutionEngine
from pi_node_supervisor.models import NodeDefinition, SensorReading
from pi_node_supervisor.planner import Planner
from pi_node_supervisor.runtime_store import RuntimeStore
from pi_node_supervisor.sensor_adapter import (
    CwtSensorAdapter,
    HuintechCwtProvider,
    SensorCommunicationError,
    SensorModbusExceptionError,
    SensorPortUnavailableError,
    SensorProtocolError,
    SensorTimeoutError,
)


class Measurement(IntFlag):
    HUMIDITY = 1
    TEMPERATURE = 2
    CONDUCTIVITY = 4
    SALINITY = 8
    TDS = 16


class ModbusCommunicationError(Exception):
    pass


class ModbusProtocolError(Exception):
    pass


class ModbusExceptionResponseError(ModbusProtocolError):
    pass


class FakeStream:
    def __init__(self):
        self.closed = False
        self.reset_count = 0

    def reset_input_buffer(self):
        self.reset_count += 1

    def close(self):
        self.closed = True


class FakeSerialModule:
    EIGHTBITS = 8
    PARITY_NONE = "N"
    STOPBITS_ONE = 1

    class SerialTimeoutException(Exception):
        pass

    def __init__(self, *, failure=None):
        self.failure = failure
        self.calls = []
        self.streams = []

    def Serial(self, port, **settings):
        self.calls.append((port, settings))
        if self.failure is not None:
            raise self.failure
        stream = FakeStream()
        self.streams.append(stream)
        return stream


class FakeDriver:
    def __init__(self, stream, slave_id, *, outcome=None):
        self.stream = stream
        self.slave_id = slave_id
        self.outcome = outcome or SimpleNamespace(temperature=21.4, humidity=64.3)
        self.calls = []

    def read_measurements(self, fields):
        self.calls.append(fields)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def cwt_node():
    return NodeDefinition.model_validate(
        {
            "id": "environment0",
            "kind": "sensor-node",
            "activity_types": ["sensor-collection"],
            "profiles": ["temperature-humidity"],
            "sensor_serial": {
                "driver": "cwt-soil-thc-s",
                "port": "/dev/ttyAMA0",
                "baudrate": 4800,
                "timeout_seconds": 1,
                "slave_id": 7,
            },
        }
    )


def modules(*, outcome=None, serial_failure=None):
    serial = FakeSerialModule(failure=serial_failure)
    drivers = []

    def create(stream, slave_id):
        driver = FakeDriver(stream, slave_id, outcome=outcome)
        drivers.append(driver)
        return driver

    driver = SimpleNamespace(
        CWTSoilTHCS=create,
        CWTSoilTHCSMeasurement=Measurement,
        ModbusCommunicationError=ModbusCommunicationError,
        ModbusProtocolError=ModbusProtocolError,
        ModbusExceptionResponseError=ModbusExceptionResponseError,
    )
    return serial, driver, drivers


def test_provider_reads_only_temperature_and_humidity_in_one_driver_call():
    serial, driver_module, drivers = modules()
    observed = datetime(2030, 1, 1, 1, 2, 3, tzinfo=UTC)
    provider = HuintechCwtProvider(
        cwt_node(),
        clock=lambda: observed,
        serial_module=serial,
        driver_module=driver_module,
    )

    first = provider.read()
    second = provider.read()
    health = provider.health()

    assert first == SensorReading(
        observed_at=observed,
        temperature_c=21.4,
        humidity_percent=64.3,
    )
    assert second == first
    assert health == {"serial_open": True}
    assert len(serial.calls) == 1
    assert serial.calls[0] == (
        "/dev/ttyAMA0",
        {
            "baudrate": 4800,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "timeout": 1.0,
            "write_timeout": 1.0,
        },
    )
    assert drivers[0].slave_id == 7
    assert drivers[0].calls == [
        Measurement.HUMIDITY | Measurement.TEMPERATURE,
        Measurement.HUMIDITY | Measurement.TEMPERATURE,
    ]
    assert not drivers[0].calls[0] & (
        Measurement.CONDUCTIVITY | Measurement.SALINITY | Measurement.TDS
    )

    provider.close()
    assert serial.streams[0].closed is True


@pytest.mark.parametrize(
    ("driver_error", "expected"),
    [
        (ModbusProtocolError("bad CRC"), SensorProtocolError),
        (ModbusExceptionResponseError("exception 2"), SensorModbusExceptionError),
    ],
)
def test_provider_classifies_protocol_failures_and_closes_port(driver_error, expected):
    serial, driver_module, _drivers = modules(outcome=driver_error)
    provider = HuintechCwtProvider(
        cwt_node(), serial_module=serial, driver_module=driver_module
    )

    with pytest.raises(expected):
        provider.read()

    assert serial.streams[0].closed is True


def test_provider_classifies_wrapped_timeout_and_closes_port():
    timeout = FakeSerialModule.SerialTimeoutException("write timeout")
    communication = ModbusCommunicationError("stream write failed")
    communication.__cause__ = timeout
    serial, driver_module, _drivers = modules(outcome=communication)
    provider = HuintechCwtProvider(
        cwt_node(), serial_module=serial, driver_module=driver_module
    )

    with pytest.raises(SensorTimeoutError):
        provider.read()

    assert serial.streams[0].closed is True


def test_provider_classifies_non_timeout_communication_failure():
    serial, driver_module, _drivers = modules(
        outcome=ModbusCommunicationError("incomplete response")
    )
    provider = HuintechCwtProvider(
        cwt_node(), serial_module=serial, driver_module=driver_module
    )

    with pytest.raises(SensorCommunicationError, match="incomplete response"):
        provider.read()

    assert serial.streams[0].closed is True


def test_provider_classifies_unavailable_port():
    serial, driver_module, _drivers = modules(
        serial_failure=PermissionError("dialout denied")
    )
    provider = HuintechCwtProvider(
        cwt_node(), serial_module=serial, driver_module=driver_module
    )

    with pytest.raises(SensorPortUnavailableError, match="dialout denied"):
        provider.read()


def test_adapter_returns_typed_measurement_and_scheduled_slot(registry):
    activity = Planner(registry).plan(
        configuration(
            {
                "id": "soil",
                "type": "sensor-collection",
                "at": "2030-01-01T08:20:00",
                "node": "environment0",
                "profile": "temperature-humidity",
            }
        )
    ).activities[0]

    class Provider:
        def read(self):
            return SensorReading(
                observed_at=datetime(2029, 12, 31, 23, 20, 1, tzinfo=UTC),
                temperature_c=18.2,
                humidity_percent=71.5,
            )

        def close(self):
            pass

    result = CwtSensorAdapter(registry.require("environment0"), Provider()).execute(
        activity, "run-1"
    )

    assert result == {
        "observed_at": "2029-12-31T23:20:01Z",
        "temperature_c": 18.2,
        "humidity_percent": 71.5,
        "scheduled_at": "2029-12-31T23:20:00+00:00",
        "run_id": "run-1",
    }


def test_executor_records_classified_sensor_failure_event(service, registry):
    at = datetime(2030, 1, 1, tzinfo=UTC)
    service.apply_document(
        {
            "schema_version": 1,
            "timezone": "UTC",
            "activities": [
                {
                    "id": "soil",
                    "type": "sensor-collection",
                    "at": at.isoformat(),
                    "node": "environment0",
                    "profile": "temperature-humidity",
                }
            ],
        }
    )

    class FailingProvider:
        def read(self):
            raise SensorProtocolError("bad CRC")

        def close(self):
            pass

    runtime = RuntimeStore(service.store.database_path)
    adapter = CwtSensorAdapter(registry.require("environment0"), FailingProvider())
    result = ExecutionEngine(
        service.store, runtime, {"environment0": adapter}
    ).tick(at)[0]

    assert result.status == "failed"
    event = runtime.list_events()[0]
    assert event.event_type == "run.failed"
    assert event.payload["error_code"] == "sensor_protocol_error"


def test_build_adapters_registers_serial_sensor_without_opening_port():
    adapters = build_adapters([cwt_node()])
    assert isinstance(adapters["environment0"], CwtSensorAdapter)


def test_engine_close_releases_sensor_provider(service):
    class Provider:
        closed = False

        def read(self):
            raise AssertionError("not called")

        def close(self):
            self.closed = True

    provider = Provider()
    adapter = CwtSensorAdapter(cwt_node(), provider)
    engine = ExecutionEngine(service.store, RuntimeStore(service.store.database_path), {
        "environment0": adapter
    })

    engine.close()

    assert provider.closed is True
