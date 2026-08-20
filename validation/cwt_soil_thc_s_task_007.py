#!/usr/bin/env python3
"""Reproducible on-device validation for HUINTECHDEVICE TASK-007."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import serial

from huintech.sensors.soil.cwt_soil_thc_s import CWTSoilTHCS


class TracingStream:
    """Record the bytes crossing the Stream boundary without changing it."""

    def __init__(self, stream: serial.Serial, frames: list[dict[str, Any]]):
        self._stream = stream
        self._frames = frames

    def read(self, size: int = -1) -> bytes:
        data = self._stream.read(size)
        self._frames.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "direction": "rx",
                "requested": size,
                "data": data.hex(" "),
            }
        )
        return data

    def write(self, data: bytes) -> int:
        written = self._stream.write(data)
        self._frames.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "direction": "tx",
                "requested": len(data),
                "written": written,
                "data": data[:written].hex(" "),
            }
        )
        return written


def json_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


class Validation:
    def __init__(self, port: str, slave_id: int, timeout: float):
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.port = port
        self.slave_id = slave_id
        self.timeout = timeout
        self.frames: list[dict[str, Any]] = []
        self.operations: list[dict[str, Any]] = []
        self.stream = serial.Serial(
            port,
            baudrate=4800,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
            write_timeout=timeout,
        )
        self.stream.reset_input_buffer()
        self.sensor = CWTSoilTHCS(
            TracingStream(self.stream, self.frames), slave_id=slave_id
        )

    def close(self) -> None:
        self.stream.close()

    def execute(
        self,
        name: str,
        operation: Callable[[], Any],
        *,
        continue_on_error: bool = False,
    ) -> Any:
        frame_start = len(self.frames)
        started = time.monotonic()
        try:
            result = operation()
        except Exception as error:
            self.operations.append(
                {
                    "name": name,
                    "status": "failed",
                    "errorType": type(error).__name__,
                    "error": str(error),
                    "elapsedSeconds": round(time.monotonic() - started, 6),
                    "frameRange": [frame_start, len(self.frames)],
                }
            )
            self.stream.reset_input_buffer()
            if continue_on_error:
                return None
            raise
        else:
            self.operations.append(
                {
                    "name": name,
                    "status": "passed",
                    "result": json_value(result),
                    "elapsedSeconds": round(time.monotonic() - started, 6),
                    "frameRange": [frame_start, len(self.frames)],
                }
            )
            return result
        finally:
            time.sleep(0.1)

    def read_only(self) -> None:
        operations = (
            ("measurements.batch", self.sensor.read_measurements),
            ("measurements.humidity", self.sensor.read_humidity),
            ("measurements.temperature", self.sensor.read_temperature),
            ("measurements.conductivity", self.sensor.read_conductivity),
            ("measurements.salinity", self.sensor.read_salinity),
            ("measurements.tds", self.sensor.read_tds),
            ("settings.conductivityFactor", self.sensor.read_conductivity_factor),
            ("settings.salinityFactor", self.sensor.read_salinity_factor),
            ("settings.tdsFactor", self.sensor.read_tds_factor),
            ("settings.temperatureOffset", self.sensor.read_temperature_offset),
            ("settings.humidityOffset", self.sensor.read_humidity_offset),
            ("settings.conductivityOffset", self.sensor.read_conductivity_offset),
            ("settings.slaveId", self.sensor.read_slave_id),
        )
        for name, operation in operations:
            self.execute(name, operation, continue_on_error=True)

    def verify_setting_writes(self) -> None:
        settings = (
            (
                "conductivityFactor",
                self.sensor.read_conductivity_factor,
                self.sensor.set_conductivity_factor,
            ),
            (
                "salinityFactor",
                self.sensor.read_salinity_factor,
                self.sensor.set_salinity_factor,
            ),
            ("tdsFactor", self.sensor.read_tds_factor, self.sensor.set_tds_factor),
            (
                "temperatureOffset",
                self.sensor.read_temperature_offset,
                self.sensor.set_temperature_offset,
            ),
            (
                "humidityOffset",
                self.sensor.read_humidity_offset,
                self.sensor.set_humidity_offset,
            ),
            (
                "conductivityOffset",
                self.sensor.read_conductivity_offset,
                self.sensor.set_conductivity_offset,
            ),
        )
        for name, reader, writer in settings:
            original = self.execute(f"settings.{name}.backup", reader)
            try:
                self.execute(
                    f"settings.{name}.writeSameValue",
                    lambda writer=writer, original=original: writer(original),
                )
                readback = self.execute(f"settings.{name}.readback", reader)
                if readback != original:
                    raise RuntimeError(
                        f"{name} readback differs: {readback!r} != {original!r}"
                    )
            finally:
                self.execute(
                    f"settings.{name}.restore",
                    lambda writer=writer, original=original: writer(original),
                )
                restored = self.execute(f"settings.{name}.verifyRestore", reader)
                if restored != original:
                    raise RuntimeError(
                        f"{name} restore differs: {restored!r} != {original!r}"
                    )

    def verify_slave_id(self) -> None:
        original = self.execute("settings.slaveId.backup", self.sensor.read_slave_id)
        candidate = 2 if original != 2 else 3
        try:
            self.execute(
                "settings.slaveId.change",
                lambda: self.sensor.set_slave_id(candidate),
            )
            changed = self.execute(
                "settings.slaveId.readback", self.sensor.read_slave_id
            )
            if changed != candidate:
                raise RuntimeError(
                    f"slave ID readback differs: {changed!r} != {candidate!r}"
                )
        finally:
            self._restore_slave_id(original, candidate)

    def _restore_slave_id(self, original: int, candidate: int) -> None:
        errors: list[str] = []
        for address in dict.fromkeys((self.sensor.slave_id, candidate, original)):
            probe = CWTSoilTHCS(
                TracingStream(self.stream, self.frames), slave_id=address
            )
            try:
                current = self.execute(
                    f"settings.slaveId.restore.probe.{address}",
                    probe.read_slave_id,
                )
                if current != original:
                    self.execute(
                        f"settings.slaveId.restore.from.{address}",
                        lambda probe=probe: probe.set_slave_id(original),
                    )
                verifier = CWTSoilTHCS(
                    TracingStream(self.stream, self.frames), slave_id=original
                )
                restored = self.execute(
                    "settings.slaveId.verifyRestore", verifier.read_slave_id
                )
                if restored != original:
                    raise RuntimeError(
                        f"slave ID restore differs: {restored!r} != {original!r}"
                    )
                self.sensor = verifier
                return
            except Exception as error:
                errors.append(f"address {address}: {type(error).__name__}: {error}")
                self.stream.reset_input_buffer()
        raise RuntimeError("CRITICAL: failed to restore slave ID; " + "; ".join(errors))

    def report(self, phase: str) -> dict[str, Any]:
        return {
            "task": "TASK-007",
            "phase": phase,
            "startedAt": self.started_at,
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "environment": {
                "hostname": platform.node(),
                "platform": platform.platform(),
                "python": sys.version,
                "pyserial": serial.VERSION,
                "port": self.port,
                "baudrate": self.stream.baudrate,
                "bytesize": self.stream.bytesize,
                "parity": self.stream.parity,
                "stopbits": self.stream.stopbits,
                "timeout": self.stream.timeout,
                "initialSlaveId": self.slave_id,
            },
            "operations": self.operations,
            "frames": self.frames,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=("read-only", "settings", "slave-id")
    )
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--slave-id", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validation = Validation(args.port, args.slave_id, args.timeout)
    status = 0
    try:
        if args.phase == "read-only":
            validation.read_only()
        elif args.phase == "settings":
            validation.verify_setting_writes()
        else:
            validation.verify_slave_id()
    except Exception as error:
        status = 1
        validation.operations.append(
            {
                "name": "phase",
                "status": "failed",
                "errorType": type(error).__name__,
                "error": str(error),
            }
        )
    finally:
        report = validation.report(args.phase)
        validation.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
