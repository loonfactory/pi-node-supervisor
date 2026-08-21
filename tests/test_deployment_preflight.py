from __future__ import annotations

from pathlib import Path

import pytest

from pi_node_supervisor.deployment_preflight import (
    SensorPreflightError,
    prepare_sensor_environment,
)
from pi_node_supervisor.models import NodeRegistryDocument


def sensor_registry(*, required: bool) -> NodeRegistryDocument:
    return NodeRegistryDocument.model_validate(
        {
            "schema_version": 1,
            "nodes": [
                {
                    "id": "environment0",
                    "kind": "sensor-node",
                    "activity_types": ["sensor-collection"],
                    "profiles": ["temperature-humidity"],
                    "required": required,
                    "sensor_serial": {
                        "driver": "cwt-soil-thc-s",
                        "port": "/dev/ttyAMA99999",
                    },
                    "sensor_storage": {
                        "local_directory": "sensors",
                    },
                }
            ],
        }
    )


def test_optional_missing_sensor_is_reported_without_blocking_deployment(
    tmp_path: Path,
) -> None:
    findings = prepare_sensor_environment(
        sensor_registry(required=False),
        tmp_path,
    )

    assert len(findings) == 1
    assert findings[0].node_id == "environment0"
    assert findings[0].port == Path("/dev/ttyAMA99999")
    assert not (tmp_path / "sensors").exists()


def test_required_missing_sensor_blocks_deployment(tmp_path: Path) -> None:
    with pytest.raises(SensorPreflightError) as raised:
        prepare_sensor_environment(sensor_registry(required=True), tmp_path)

    assert raised.value.finding.node_id == "environment0"
    assert raised.value.finding.port == Path("/dev/ttyAMA99999")
