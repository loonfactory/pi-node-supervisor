import unittest

from huintech.sensors.soil.cwt_soil_thc_s import (
    CWTSoilTHCS,
    CWTSoilTHCSMeasurement,
    CWTSoilTHCSMeasurements,
    ModbusCommunicationError,
    ModbusExceptionResponseError,
    ModbusProtocolError,
)


def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def frame(data: bytes) -> bytes:
    return data + modbus_crc(data).to_bytes(2, "little")


def read_request(register: int, count: int, slave_id: int = 1) -> bytes:
    return frame(
        bytes((slave_id, 0x03))
        + register.to_bytes(2, "big")
        + count.to_bytes(2, "big")
    )


def read_response(*values: int, slave_id: int = 1) -> bytes:
    payload = b"".join(value.to_bytes(2, "big") for value in values)
    return frame(bytes((slave_id, 0x03, len(payload))) + payload)


def write_frame(register: int, value: int, slave_id: int = 1) -> bytes:
    return frame(
        bytes((slave_id, 0x06))
        + register.to_bytes(2, "big")
        + value.to_bytes(2, "big")
    )


class FakeStream:
    def __init__(
        self,
        response: bytes = b"",
        *,
        read_chunk_size: int | None = None,
        write_chunk_size: int | None = None,
    ):
        self._response = response
        self._read_chunk_size = read_chunk_size
        self._write_chunk_size = write_chunk_size
        self.written = bytearray()
        self.read_sizes: list[int] = []
        self.baudrate = 4800
        self.transport_marker = object()

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if not self._response:
            return b""

        available = len(self._response)
        requested = available if size < 0 else min(size, available)
        if self._read_chunk_size is not None:
            requested = min(requested, self._read_chunk_size)
        chunk, self._response = (
            self._response[:requested],
            self._response[requested:],
        )
        return chunk

    def write(self, data: bytes) -> int:
        size = len(data)
        if self._write_chunk_size is not None:
            size = min(size, self._write_chunk_size)
        self.written.extend(data[:size])
        return size


class ManualFrameTests(unittest.TestCase):
    def test_manual_read_example_request_and_values(self) -> None:
        response = bytes.fromhex("01 03 06 02 92 FF 9B 03 E8 D8 0F")
        stream = FakeStream(
            response,
            read_chunk_size=2,
            write_chunk_size=3,
        )
        sensor = CWTSoilTHCS(stream)
        fields = (
            CWTSoilTHCSMeasurement.HUMIDITY
            | CWTSoilTHCSMeasurement.TEMPERATURE
            | CWTSoilTHCSMeasurement.CONDUCTIVITY
        )

        result = sensor.read_measurements(fields)

        self.assertEqual(
            result,
            CWTSoilTHCSMeasurements(
                humidity=65.8,
                temperature=-10.1,
                conductivity=1000,
            ),
        )
        self.assertEqual(
            bytes(stream.written),
            bytes.fromhex("01 03 00 00 00 03 05 CB"),
        )

    def test_manual_read_example_printed_crc_is_rejected(self) -> None:
        documented_response = bytes.fromhex(
            "01 03 06 02 92 FF 9B 03 E8 38 75"
        )
        sensor = CWTSoilTHCS(FakeStream(documented_response))
        fields = (
            CWTSoilTHCSMeasurement.HUMIDITY
            | CWTSoilTHCSMeasurement.TEMPERATURE
            | CWTSoilTHCSMeasurement.CONDUCTIVITY
        )

        with self.assertRaises(ModbusProtocolError):
            sensor.read_measurements(fields)

    def test_manual_write_slave_id_example(self) -> None:
        example = bytes.fromhex("01 06 07 D0 00 02 08 86")
        stream = FakeStream(example, read_chunk_size=2, write_chunk_size=3)
        sensor = CWTSoilTHCS(stream)

        sensor.set_slave_id(2)

        self.assertEqual(bytes(stream.written), example)
        self.assertEqual(sensor.slave_id, 2)


class MeasurementTests(unittest.TestCase):
    def test_full_measurement_read(self) -> None:
        stream = FakeStream(read_response(658, 0xFF9B, 1000, 55, 50))
        sensor = CWTSoilTHCS(stream)

        result = sensor.read_measurements()

        self.assertEqual(
            result,
            CWTSoilTHCSMeasurements(65.8, -10.1, 1000, 55, 50),
        )
        self.assertIsInstance(result.humidity, float)
        self.assertIsInstance(result.temperature, float)
        self.assertIsInstance(result.conductivity, int)
        self.assertIsInstance(result.salinity, int)
        self.assertIsInstance(result.tds, int)
        self.assertEqual(bytes(stream.written), read_request(0x0000, 5))

    def test_noncontiguous_selection_uses_one_minimal_range(self) -> None:
        stream = FakeStream(read_response(658, 0xFF9B, 1000, 55))
        sensor = CWTSoilTHCS(stream)
        fields = (
            CWTSoilTHCSMeasurement.HUMIDITY
            | CWTSoilTHCSMeasurement.SALINITY
        )

        result = sensor.read_measurements(fields)

        self.assertEqual(
            result,
            CWTSoilTHCSMeasurements(humidity=65.8, salinity=55),
        )
        self.assertEqual(bytes(stream.written), read_request(0x0000, 4))

    def test_individual_measurement_reads(self) -> None:
        cases = (
            ("read_humidity", 0x0000, 658, 65.8, float),
            ("read_temperature", 0x0001, 0xFF9B, -10.1, float),
            ("read_conductivity", 0x0002, 1000, 1000, int),
            ("read_salinity", 0x0003, 55, 55, int),
            ("read_tds", 0x0004, 50, 50, int),
        )
        for method, register, raw, expected, expected_type in cases:
            with self.subTest(method=method):
                stream = FakeStream(read_response(raw))
                sensor = CWTSoilTHCS(stream)

                result = getattr(sensor, method)()

                self.assertEqual(result, expected)
                self.assertIsInstance(result, expected_type)
                self.assertEqual(
                    bytes(stream.written),
                    read_request(register, 1),
                )


class SettingTests(unittest.TestCase):
    def assert_stream_settings_unchanged(
        self, stream: FakeStream, baudrate: int, marker: object
    ) -> None:
        self.assertEqual(stream.baudrate, baudrate)
        self.assertIs(stream.transport_marker, marker)

    def test_setting_reads(self) -> None:
        cases = (
            ("read_conductivity_factor", 0x0022, 55, 5.5),
            ("read_salinity_factor", 0x0023, 55, 0.55),
            ("read_tds_factor", 0x0024, 50, 0.5),
            ("read_temperature_offset", 0x0050, 0xFFF6, -1.0),
            ("read_humidity_offset", 0x0051, 15, 1.5),
            ("read_conductivity_offset", 0x0052, 0xFF9C, -100),
            ("read_slave_id", 0x07D0, 1, 1),
        )
        for method, register, raw, expected in cases:
            with self.subTest(method=method):
                stream = FakeStream(read_response(raw))
                marker = stream.transport_marker
                sensor = CWTSoilTHCS(stream)

                self.assertEqual(getattr(sensor, method)(), expected)
                self.assertEqual(
                    bytes(stream.written),
                    read_request(register, 1),
                )
                self.assert_stream_settings_unchanged(stream, 4800, marker)

    def test_setting_writes(self) -> None:
        cases = (
            ("set_conductivity_factor", 0x0022, 5.5, 55),
            ("set_salinity_factor", 0x0023, 0.55, 55),
            ("set_tds_factor", 0x0024, 0.5, 50),
            ("set_temperature_offset", 0x0050, -1.0, 0xFFF6),
            ("set_humidity_offset", 0x0051, 1.5, 15),
            ("set_conductivity_offset", 0x0052, -100, 0xFF9C),
        )
        for method, register, value, raw in cases:
            with self.subTest(method=method):
                response = write_frame(register, raw)
                stream = FakeStream(response)
                marker = stream.transport_marker
                sensor = CWTSoilTHCS(stream)

                getattr(sensor, method)(value)

                self.assertEqual(bytes(stream.written), response)
                self.assert_stream_settings_unchanged(stream, 4800, marker)

    def test_setting_values_are_validated_before_io(self) -> None:
        cases = (
            ("set_conductivity_factor", -0.1),
            ("set_conductivity_factor", 10.1),
            ("set_salinity_factor", 1.01),
            ("set_tds_factor", 0.555),
            ("set_temperature_offset", 0.01),
            ("set_humidity_offset", float("nan")),
            ("set_conductivity_offset", 32768),
            ("set_slave_id", 0),
            ("set_slave_id", 255),
        )
        for method, value in cases:
            with self.subTest(method=method, value=value):
                stream = FakeStream()
                sensor = CWTSoilTHCS(stream)

                with self.assertRaises((TypeError, ValueError)):
                    getattr(sensor, method)(value)

                self.assertEqual(bytes(stream.written), b"")

    def test_failed_slave_id_write_preserves_state(self) -> None:
        mismatched_echo = write_frame(0x07D0, 3)
        stream = FakeStream(mismatched_echo)
        sensor = CWTSoilTHCS(stream)

        with self.assertRaises(ModbusProtocolError):
            sensor.set_slave_id(2)

        self.assertEqual(sensor.slave_id, 1)


class ErrorResponseTests(unittest.TestCase):
    def test_crc_mismatch(self) -> None:
        response = bytearray(read_response(658))
        response[-1] ^= 0xFF

        with self.assertRaises(ModbusProtocolError):
            CWTSoilTHCS(FakeStream(bytes(response))).read_humidity()

    def test_wrong_slave_id(self) -> None:
        with self.assertRaises(ModbusProtocolError):
            CWTSoilTHCS(
                FakeStream(read_response(658, slave_id=2))
            ).read_humidity()

    def test_wrong_function_code(self) -> None:
        response = frame(bytes((1, 0x04, 2, 0x02, 0x92)))

        with self.assertRaises(ModbusProtocolError):
            CWTSoilTHCS(FakeStream(response)).read_humidity()

    def test_modbus_exception_response(self) -> None:
        response = frame(bytes((1, 0x83, 0x02)))

        with self.assertRaises(ModbusExceptionResponseError) as caught:
            CWTSoilTHCS(FakeStream(response)).read_humidity()

        self.assertEqual(caught.exception.function_code, 0x03)
        self.assertEqual(caught.exception.exception_code, 0x02)

    def test_short_response(self) -> None:
        with self.assertRaises(ModbusCommunicationError):
            CWTSoilTHCS(FakeStream(bytes((1, 0x03)))).read_humidity()


if __name__ == "__main__":
    unittest.main()
