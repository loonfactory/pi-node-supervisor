"""Deployment checks for optional and required sensor hardware."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .models import NodeDefinition, NodeRegistryDocument


@dataclass(frozen=True, slots=True)
class SensorUnavailable:
    node_id: str
    port: Path
    reason: str


class SensorPreflightError(RuntimeError):
    def __init__(self, finding: SensorUnavailable) -> None:
        super().__init__(
            f"required sensor {finding.node_id} is unavailable at "
            f"{finding.port}: {finding.reason}"
        )
        self.finding = finding


def prepare_sensor_environment(
    registry: NodeRegistryDocument,
    supervisor_base: Path,
) -> tuple[SensorUnavailable, ...]:
    unavailable: list[SensorUnavailable] = []
    base = supervisor_base.resolve()
    for node in registry.nodes:
        if node.sensor_serial is None:
            continue
        port = Path(node.sensor_serial.port)
        finding = _port_finding(node, port)
        if finding is not None:
            if node.required:
                raise SensorPreflightError(finding)
            unavailable.append(finding)
            continue
        _prepare_storage(node, base)
    return tuple(unavailable)


def _port_finding(node: NodeDefinition, port: Path) -> SensorUnavailable | None:
    if not port.exists():
        return SensorUnavailable(node.id, port, "port does not exist")
    if not os.access(port, os.R_OK | os.W_OK):
        return SensorUnavailable(node.id, port, "port is not readable and writable")
    return None


def _prepare_storage(node: NodeDefinition, base: Path) -> None:
    if node.sensor_storage is None:
        raise RuntimeError(f"sensor storage is not configured: {node.id}")
    configured_root = node.sensor_storage.local_directory
    storage_root = (
        configured_root if configured_root.is_absolute() else base / configured_root
    ).resolve()
    try:
        storage_root.relative_to(base)
        below_base = True
    except ValueError:
        below_base = False
    if not storage_root.exists() and below_base:
        storage_root.mkdir(parents=True, mode=0o700)
    if not storage_root.is_dir() or not os.access(
        storage_root, os.R_OK | os.W_OK | os.X_OK
    ):
        raise RuntimeError(
            f"sensor CSV directory requires service-user access: {storage_root}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("registry", type=Path)
    parser.add_argument("supervisor_base", type=Path)
    args = parser.parse_args(argv)
    registry = NodeRegistryDocument.model_validate_json(
        args.registry.read_text(encoding="utf-8")
    )
    try:
        findings = prepare_sensor_environment(registry, args.supervisor_base)
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for finding in findings:
        print(
            f"warning: optional sensor {finding.node_id} is unavailable at "
            f"{finding.port}: {finding.reason}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
