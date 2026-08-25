"""Deployment checks derived from the single active v2 configuration."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .config_v2 import ResolvedConfiguration, resolve_configuration


@dataclass(frozen=True)
class SensorUnavailable:
    device_id: str
    port: Path
    reason: str


class SensorPreflightError(RuntimeError):
    def __init__(self, finding: SensorUnavailable) -> None:
        super().__init__(
            f"sensor {finding.device_id} is unavailable at "
            f"{finding.port}: {finding.reason}"
        )
        self.finding = finding


def prepare_sensor_environment(
    configuration: ResolvedConfiguration,
) -> None:
    for device_id, sensor in configuration.sensors.items():
        port = Path(sensor.driver.port)
        if not port.exists():
            raise SensorPreflightError(
                SensorUnavailable(device_id, port, "port does not exist")
            )
        if not os.access(port, os.R_OK | os.W_OK):
            raise SensorPreflightError(
                SensorUnavailable(
                    device_id, port, "port is not readable and writable"
                )
            )
        for storage in sensor.storages:
            if storage.type != "local":
                continue
            root = Path(storage.final_path)
            root.mkdir(parents=True, exist_ok=True, mode=0o750)
            if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
                raise RuntimeError(
                    f"local Storage requires service-user access: {root}"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.config.read_text(encoding="utf-8"))
        prepare_sensor_environment(resolve_configuration(document).resolved)
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
