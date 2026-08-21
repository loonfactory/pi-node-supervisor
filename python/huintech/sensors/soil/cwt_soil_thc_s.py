"""Driver for the CWT-Soil-THC-S soil sensor."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import IntFlag
from typing import cast

from huintech.stream import Stream


class CWTSoilTHCSMeasurement(IntFlag):
    """Measurement fields that can be read in one contiguous request."""

    HUMIDITY = 1 << 0
    TEMPERATURE = 1 << 1
    CONDUCTIVITY = 1 << 2
    SALINITY = 1 << 3
    TDS = 1 << 4
    ALL = HUMIDITY | TEMPERATURE | CONDUCTIVITY | SALINITY | TDS


@dataclass
class CWTSoilTHCSMeasurements:
    """Decoded soil measurements; unrequested fields are ``None``."""

    humidity: float | None = None
    temperature: float | None = None
    conductivity: int | None = None
    salinity: int | None = None
    tds: int | None = None


class ModbusError(Exception):
    """Base error raised while communicating over Modbus."""


class ModbusCommunicationError(ModbusError):
    """The injected stream could not complete a request or response."""


class ModbusProtocolError(ModbusError):
    """A sensor response did not match the expected Modbus RTU frame."""


class ModbusExceptionResponseError(ModbusProtocolError):
    """The sensor returned a Modbus exception response."""

    def __init__(self, function_code: int, exception_code: int):
        self.function_code = function_code
        self.exception_code = exception_code
        super().__init__(
            f"Modbus function 0x{function_code:02X} failed "
            f"with exception 0x{exception_code:02X}"
        )


class CWTSoilTHCS:
    _READ_HOLDING_REGISTERS = 0x03
    _WRITE_SINGLE_REGISTER = 0x06

    _HUMIDITY_REGISTER = 0x0000
    _TEMPERATURE_REGISTER = 0x0001
    _CONDUCTIVITY_REGISTER = 0x0002
    _SALINITY_REGISTER = 0x0003
    _TDS_REGISTER = 0x0004

    _MEASUREMENT_REGISTERS = {
        CWTSoilTHCSMeasurement.HUMIDITY: _HUMIDITY_REGISTER,
        CWTSoilTHCSMeasurement.TEMPERATURE: _TEMPERATURE_REGISTER,
        CWTSoilTHCSMeasurement.CONDUCTIVITY: _CONDUCTIVITY_REGISTER,
        CWTSoilTHCSMeasurement.SALINITY: _SALINITY_REGISTER,
        CWTSoilTHCSMeasurement.TDS: _TDS_REGISTER,
    }

    _CONDUCTIVITY_FACTOR_REGISTER = 0x0022
    _SALINITY_FACTOR_REGISTER = 0x0023
    _TDS_FACTOR_REGISTER = 0x0024
    _TEMPERATURE_OFFSET_REGISTER = 0x0050
    _HUMIDITY_OFFSET_REGISTER = 0x0051
    _CONDUCTIVITY_OFFSET_REGISTER = 0x0052
    _SLAVE_ID_REGISTER = 0x07D0

    def __init__(self, stream: Stream, slave_id: int = 1):
        self._validate_slave_id(slave_id)

        self._stream = stream
        self._slave_id = slave_id

    @property
    def slave_id(self) -> int:
        """Slave ID currently used by this driver instance."""

        return self._slave_id

    @staticmethod
    def _crc16(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    @classmethod
    def _with_crc(cls, data: bytes) -> bytes:
        return data + cls._crc16(data).to_bytes(2, byteorder="little")

    @staticmethod
    def _validate_register(register: int) -> None:
        if not 0 <= register <= 0xFFFF:
            raise ValueError("register must be between 0 and 65535")

    @staticmethod
    def _validate_slave_id(slave_id: int) -> None:
        if isinstance(slave_id, bool) or not isinstance(slave_id, int):
            raise TypeError("slave_id must be an integer")
        if not 1 <= slave_id <= 254:
            raise ValueError("slave_id must be between 1 and 254")

    @staticmethod
    def _encode_scaled_value(
        name: str,
        value: float | int,
        multiplier: int,
        minimum_raw: int,
        maximum_raw: int,
    ) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a number")

        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError(f"{name} must be finite") from error
        if not decimal_value.is_finite():
            raise ValueError(f"{name} must be finite")

        scaled = decimal_value * multiplier
        integral = scaled.to_integral_value()
        if scaled != integral:
            resolution = Decimal(1) / multiplier
            raise ValueError(
                f"{name} must use increments of {resolution}"
            )

        raw_value = int(integral)
        if not minimum_raw <= raw_value <= maximum_raw:
            minimum = Decimal(minimum_raw) / multiplier
            maximum = Decimal(maximum_raw) / multiplier
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return raw_value

    @classmethod
    def _encode_factor(
        cls, name: str, value: float, multiplier: int
    ) -> int:
        return cls._encode_scaled_value(name, value, multiplier, 0, 100)

    @staticmethod
    def _decode_factor(name: str, raw_value: int, multiplier: int) -> float:
        if not 0 <= raw_value <= 100:
            raise ModbusProtocolError(
                f"invalid {name} register value: {raw_value}"
            )
        return raw_value / multiplier

    @classmethod
    def _encode_offset(
        cls, name: str, value: float | int, multiplier: int
    ) -> int:
        raw_value = cls._encode_scaled_value(
            name,
            value,
            multiplier,
            -0x8000,
            0x7FFF,
        )
        return raw_value & 0xFFFF

    @staticmethod
    def _decode_offset(raw_value: int, multiplier: int) -> float | int:
        if raw_value & 0x8000:
            raw_value -= 0x10000
        if multiplier == 1:
            return raw_value
        return raw_value / multiplier

    def _write_all(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            try:
                written = self._stream.write(data[offset:])
            except Exception as error:
                raise ModbusCommunicationError("stream write failed") from error

            if not isinstance(written, int) or written <= 0:
                raise ModbusCommunicationError(
                    "stream write did not make progress"
                )
            if written > len(data) - offset:
                raise ModbusCommunicationError(
                    "stream reported writing more bytes than requested"
                )
            offset += written

    def _read_exactly(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            try:
                chunk = self._stream.read(remaining)
            except Exception as error:
                raise ModbusCommunicationError("stream read failed") from error

            if not isinstance(chunk, bytes):
                raise ModbusCommunicationError("stream read did not return bytes")
            if not chunk:
                received = size - remaining
                raise ModbusCommunicationError(
                    f"incomplete response: expected {size} bytes, received {received}"
                )
            if len(chunk) > remaining:
                raise ModbusCommunicationError(
                    "stream returned more bytes than requested"
                )

            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _validate_response(
        self,
        response: bytes,
        expected_function: int,
        expected_length: int,
    ) -> None:
        if len(response) != expected_length:
            raise ModbusProtocolError(
                f"invalid response length: expected {expected_length}, "
                f"received {len(response)}"
            )
        if self._crc16(response[:-2]) != int.from_bytes(
            response[-2:], byteorder="little"
        ):
            raise ModbusProtocolError("response CRC does not match")
        if response[0] != self._slave_id:
            raise ModbusProtocolError(
                f"unexpected slave ID: expected {self._slave_id}, received {response[0]}"
            )
        if response[1] != expected_function:
            raise ModbusProtocolError(
                f"unexpected function code: expected 0x{expected_function:02X}, "
                f"received 0x{response[1]:02X}"
            )

    def _raise_modbus_exception(
        self, response: bytes, expected_function: int
    ) -> None:
        exception_function = expected_function | 0x80
        self._validate_response(response, exception_function, 5)
        raise ModbusExceptionResponseError(expected_function, response[2])

    def _read_holding_registers(self, register: int, count: int) -> list[int]:
        self._validate_register(register)
        if not 1 <= count <= 125:
            raise ValueError("count must be between 1 and 125")
        if register + count - 1 > 0xFFFF:
            raise ValueError("requested register range exceeds 65535")

        request = bytes((self._slave_id, self._READ_HOLDING_REGISTERS))
        request += register.to_bytes(2, byteorder="big")
        request += count.to_bytes(2, byteorder="big")
        self._write_all(self._with_crc(request))

        prefix = self._read_exactly(2)
        if prefix[1] & 0x80:
            self._raise_modbus_exception(
                prefix + self._read_exactly(3), self._READ_HOLDING_REGISTERS
            )

        byte_count = self._read_exactly(1)
        response = prefix + byte_count + self._read_exactly(byte_count[0] + 2)
        self._validate_response(
            response,
            self._READ_HOLDING_REGISTERS,
            3 + count * 2 + 2,
        )
        if byte_count[0] != count * 2:
            raise ModbusProtocolError(
                f"invalid byte count: expected {count * 2}, received {byte_count[0]}"
            )

        payload = response[3:-2]
        return [
            int.from_bytes(payload[offset : offset + 2], byteorder="big")
            for offset in range(0, len(payload), 2)
        ]

    def _write_single_register(self, register: int, value: int) -> None:
        self._validate_register(register)
        if not 0 <= value <= 0xFFFF:
            raise ValueError("value must be between 0 and 65535")

        request = bytes((self._slave_id, self._WRITE_SINGLE_REGISTER))
        request += register.to_bytes(2, byteorder="big")
        request += value.to_bytes(2, byteorder="big")
        request = self._with_crc(request)
        self._write_all(request)

        prefix = self._read_exactly(2)
        if prefix[1] & 0x80:
            self._raise_modbus_exception(
                prefix + self._read_exactly(3), self._WRITE_SINGLE_REGISTER
            )

        response = prefix + self._read_exactly(6)
        self._validate_response(response, self._WRITE_SINGLE_REGISTER, 8)
        if response != request:
            raise ModbusProtocolError(
                "write response register or value does not match the request"
            )

    @staticmethod
    def _decode_temperature(value: int) -> float:
        if value & 0x8000:
            value -= 0x10000
        return value / 10.0

    def read_measurements(
        self,
        measurements: CWTSoilTHCSMeasurement = CWTSoilTHCSMeasurement.ALL,
    ) -> CWTSoilTHCSMeasurements:
        """Read selected measurements using one minimal contiguous request."""

        if not isinstance(measurements, CWTSoilTHCSMeasurement):
            raise TypeError(
                "measurements must be a CWTSoilTHCSMeasurement value"
            )
        if not measurements:
            raise ValueError("at least one measurement must be selected")

        unknown_bits = int(measurements) & ~int(CWTSoilTHCSMeasurement.ALL)
        if unknown_bits:
            raise ValueError(
                f"measurements contains unsupported flags: 0x{unknown_bits:X}"
            )

        selected_registers = [
            register
            for measurement, register in self._MEASUREMENT_REGISTERS.items()
            if measurements & measurement
        ]
        first_register = min(selected_registers)
        last_register = max(selected_registers)
        registers = self._read_holding_registers(
            first_register,
            last_register - first_register + 1,
        )

        def register_value(register: int) -> int:
            return registers[register - first_register]

        return CWTSoilTHCSMeasurements(
            humidity=(
                register_value(self._HUMIDITY_REGISTER) / 10.0
                if measurements & CWTSoilTHCSMeasurement.HUMIDITY
                else None
            ),
            temperature=(
                self._decode_temperature(
                    register_value(self._TEMPERATURE_REGISTER)
                )
                if measurements & CWTSoilTHCSMeasurement.TEMPERATURE
                else None
            ),
            conductivity=(
                register_value(self._CONDUCTIVITY_REGISTER)
                if measurements & CWTSoilTHCSMeasurement.CONDUCTIVITY
                else None
            ),
            salinity=(
                register_value(self._SALINITY_REGISTER)
                if measurements & CWTSoilTHCSMeasurement.SALINITY
                else None
            ),
            tds=(
                register_value(self._TDS_REGISTER)
                if measurements & CWTSoilTHCSMeasurement.TDS
                else None
            ),
        )

    def read_humidity(self) -> float:
        value = self.read_measurements(
            CWTSoilTHCSMeasurement.HUMIDITY
        ).humidity
        return cast(float, value)

    def read_temperature(self) -> float:
        value = self.read_measurements(
            CWTSoilTHCSMeasurement.TEMPERATURE
        ).temperature
        return cast(float, value)

    def read_conductivity(self) -> int:
        value = self.read_measurements(
            CWTSoilTHCSMeasurement.CONDUCTIVITY
        ).conductivity
        return cast(int, value)

    def read_salinity(self) -> int:
        value = self.read_measurements(
            CWTSoilTHCSMeasurement.SALINITY
        ).salinity
        return cast(int, value)

    def read_tds(self) -> int:
        value = self.read_measurements(CWTSoilTHCSMeasurement.TDS).tds
        return cast(int, value)

    def _read_single_register(self, register: int) -> int:
        return self._read_holding_registers(register, 1)[0]

    def read_conductivity_factor(self) -> float:
        raw_value = self._read_single_register(
            self._CONDUCTIVITY_FACTOR_REGISTER
        )
        return self._decode_factor("conductivity factor", raw_value, 10)

    def set_conductivity_factor(self, value: float) -> None:
        raw_value = self._encode_factor("conductivity factor", value, 10)
        self._write_single_register(
            self._CONDUCTIVITY_FACTOR_REGISTER, raw_value
        )

    def read_salinity_factor(self) -> float:
        raw_value = self._read_single_register(self._SALINITY_FACTOR_REGISTER)
        return self._decode_factor("salinity factor", raw_value, 100)

    def set_salinity_factor(self, value: float) -> None:
        raw_value = self._encode_factor("salinity factor", value, 100)
        self._write_single_register(self._SALINITY_FACTOR_REGISTER, raw_value)

    def read_tds_factor(self) -> float:
        raw_value = self._read_single_register(self._TDS_FACTOR_REGISTER)
        return self._decode_factor("TDS factor", raw_value, 100)

    def set_tds_factor(self, value: float) -> None:
        raw_value = self._encode_factor("TDS factor", value, 100)
        self._write_single_register(self._TDS_FACTOR_REGISTER, raw_value)

    def read_temperature_offset(self) -> float:
        raw_value = self._read_single_register(
            self._TEMPERATURE_OFFSET_REGISTER
        )
        value = self._decode_offset(raw_value, 10)
        return float(value)

    def set_temperature_offset(self, value: float) -> None:
        raw_value = self._encode_offset("temperature offset", value, 10)
        self._write_single_register(
            self._TEMPERATURE_OFFSET_REGISTER, raw_value
        )

    def read_humidity_offset(self) -> float:
        raw_value = self._read_single_register(self._HUMIDITY_OFFSET_REGISTER)
        value = self._decode_offset(raw_value, 10)
        return float(value)

    def set_humidity_offset(self, value: float) -> None:
        raw_value = self._encode_offset("humidity offset", value, 10)
        self._write_single_register(self._HUMIDITY_OFFSET_REGISTER, raw_value)

    def read_conductivity_offset(self) -> int:
        raw_value = self._read_single_register(
            self._CONDUCTIVITY_OFFSET_REGISTER
        )
        value = self._decode_offset(raw_value, 1)
        return int(value)

    def set_conductivity_offset(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("conductivity offset must be an integer")
        raw_value = self._encode_offset("conductivity offset", value, 1)
        self._write_single_register(
            self._CONDUCTIVITY_OFFSET_REGISTER, raw_value
        )

    def read_slave_id(self) -> int:
        slave_id = self._read_single_register(self._SLAVE_ID_REGISTER)
        try:
            self._validate_slave_id(slave_id)
        except (TypeError, ValueError) as error:
            raise ModbusProtocolError(
                f"invalid slave ID register value: {slave_id}"
            ) from error
        return slave_id

    def set_slave_id(self, slave_id: int) -> None:
        self._validate_slave_id(slave_id)
        self._write_single_register(self._SLAVE_ID_REGISTER, slave_id)
        self._slave_id = slave_id
