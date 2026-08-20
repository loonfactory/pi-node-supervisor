"""Driver for the CWT-Soil-THC-S soil sensor."""

from huintech.stream import Stream


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

    def __init__(self, stream: Stream, slave_id: int = 1):
        if not 0 <= slave_id <= 0xFF:
            raise ValueError("slave_id must fit in one byte")

        self._stream = stream
        self._slave_id = slave_id

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
