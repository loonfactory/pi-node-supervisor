from __future__ import annotations

from pathlib import Path

import pytest

from pi_node_supervisor.models import NodeRegistryDocument, SupervisorConfiguration
from pi_node_supervisor.registry import NodeRegistry
from pi_node_supervisor.service import SupervisorService
from pi_node_supervisor.storage import RevisionStore


@pytest.fixture
def registry() -> NodeRegistry:
    return NodeRegistry(
        NodeRegistryDocument.model_validate(
            {
                "schema_version": 1,
                "nodes": [
                    {
                        "id": "camera0",
                        "kind": "capture-node",
                        "activity_types": ["capture"],
                        "profiles": ["default", "night"],
                        "boot_ready_seconds": 15,
                        "prepare_seconds": 5,
                        "default_runtime_seconds": 5,
                        "cleanup_seconds": 10,
                    },
                    {
                        "id": "environment0",
                        "kind": "sensor-node",
                        "activity_types": ["sensor-collection"],
                        "profiles": ["temperature-humidity"],
                        "boot_ready_seconds": 10,
                        "prepare_seconds": 0,
                        "default_runtime_seconds": 10,
                        "cleanup_seconds": 5,
                    },
                ],
            }
        )
    )


def configuration(*activities, timezone="Asia/Seoul", merge_seconds=1800):
    return SupervisorConfiguration.model_validate(
        {
            "schema_version": 1,
            "timezone": timezone,
            "policies": {
                "boot-lead-seconds": 120,
                "shutdown-grace-seconds": 60,
                "merge-power-gaps-shorter-than-seconds": merge_seconds,
                "minimum-lead-seconds": 60,
                "clock-tolerance-seconds": 5,
            },
            "activities": list(activities),
        }
    )


@pytest.fixture
def service(tmp_path: Path, registry: NodeRegistry) -> SupervisorService:
    return SupervisorService(
        registry,
        RevisionStore(tmp_path / "state"),
        active_configuration_path=tmp_path / "etc" / "supervisor.json",
        power_manager_configuration_path=tmp_path / "etc" / "power.json",
    )

