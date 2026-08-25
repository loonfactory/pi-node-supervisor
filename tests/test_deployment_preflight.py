from __future__ import annotations

from pathlib import Path

import pytest

from pi_node_supervisor.config_v2 import resolve_configuration
from pi_node_supervisor.deployment_preflight import (
    SensorPreflightError,
    prepare_sensor_environment,
)


def test_missing_configured_sensor_blocks_deployment() -> None:
    resolved = resolve_configuration(
        {
            "schemaVersion": 2,
            "sensors": {
                "devices": {
                    "soil": {
                        "driver": {
                            "name": "cwt_soil_thc_s",
                            "port": "/dev/ttyAMA99999",
                            "unitId": 1,
                        }
                    }
                }
            },
            "power": {"minOffSeconds": 300},
        }
    ).resolved

    with pytest.raises(SensorPreflightError) as raised:
        prepare_sensor_environment(resolved)

    assert raised.value.finding.device_id == "soil"
    assert raised.value.finding.port == Path("/dev/ttyAMA99999")
