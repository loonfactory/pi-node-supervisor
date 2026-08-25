from __future__ import annotations

import pytest

from pi_node_supervisor.config_v2 import resolve_configuration


def configuration() -> dict:
    return {
        "schemaVersion": 2,
        "policies": {"clockToleranceSeconds": 10},
        "schedules": {
            "daytime": [
                {
                    "type": "interval",
                    "startTime": "05:00:00",
                    "endTime": "20:00:00",
                    "intervalSeconds": 1800,
                }
            ],
            "allDay": [{"type": "daily", "time": "12:00:00"}],
        },
        "storages": {
            "local": {"type": "local", "basePath": "/data/capture"},
            "nas": {
                "type": "sftp",
                "host": "nas.local",
                "username": "capture",
                "basePath": "/archive/capture",
                "privateKeyPath": "/etc/pi-node-supervisor/id_ed25519",
                "knownHostsPath": "/etc/ssh/ssh_known_hosts",
            },
        },
        "cameras": {
            "defaults": {
                "captureNode": {
                    "images": {"dng": {"embedMetadata": True}},
                },
                "schedule": "daytime",
                "storages": [
                    {"extends": "#/storages/local"},
                    {"extends": "#/storages/nas"},
                ],
            },
            "devices": {
                "rgb": {
                    "captureNode": {
                        "camera": {"type": "picamera2", "cameraNum": 0}
                    }
                },
                "noir": {
                    "captureNode": {
                        "camera": {"type": "picamera2", "cameraNum": 1}
                    },
                    "storages": [
                        {"extends": "#/storages/nas", "path": "infrared"}
                    ],
                },
            },
        },
        "sensors": {
            "defaults": {
                "schedule": "allDay",
                "storages": [{"extends": "#/storages/local"}],
            },
            "devices": {
                "soil": {
                    "driver": {
                        "name": "cwt_soil_thc_s",
                        "port": "/dev/ttyAMA0",
                        "baudRate": 4800,
                        "unitId": 1,
                    }
                }
            },
        },
        "power": {
            "bootLeadSeconds": 45,
            "shutdownGraceSeconds": 30,
            "minOffSeconds": 300,
            "windows": [],
        },
    }


def test_resolver_builds_complete_device_runtime_configuration() -> None:
    result = resolve_configuration(configuration())

    rgb = result.resolved.cameras["rgb"]
    assert rgb.capture_node["camera"]["cameraNum"] == 0
    assert [item.final_path for item in rgb.storages] == [
        "/data/capture/rgb",
        "/archive/capture/rgb",
    ]
    noir = result.resolved.cameras["noir"]
    assert [item.final_path for item in noir.storages] == [
        "/archive/capture/infrared"
    ]
    assert result.resolved.sensors["soil"].driver.port == "/dev/ttyAMA0"


def test_generated_capture_node_document_excludes_supervisor_owned_sections() -> None:
    document = configuration()
    document["cameras"]["devices"]["rgb"]["captureNode"].update(
        {"captures": [{"type": "daily", "time": "12:00:00"}], "storages": [{}]}
    )

    generated = resolve_configuration(document).capture_node_configs["rgb"]

    assert "captures" not in generated
    assert "storages" not in generated
    assert generated["camera"]["cameraNum"] == 0


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda value: value["cameras"]["defaults"].update(
                {"schedule": "missing"}
            ),
            "schedule does not exist",
        ),
        (
            lambda value: value["cameras"]["defaults"]["storages"].append(
                {"extends": "#/storages/missing"}
            ),
            "storage does not exist",
        ),
        (
            lambda value: value["cameras"]["defaults"]["storages"].append(
                {"extends": "#/storages/local", "path": "../escape"}
            ),
            "normalized and relative",
        ),
    ],
)
def test_resolver_rejects_broken_runtime_references(mutation, message) -> None:
    document = configuration()
    mutation(document)
    with pytest.raises(ValueError, match=message):
        resolve_configuration(document)


def test_resolver_rejects_duplicate_sensor_bus_address() -> None:
    document = configuration()
    document["sensors"]["devices"]["soilBackup"] = {
        "driver": {
            "name": "cwt_soil_thc_s",
            "port": "/dev/ttyAMA0",
            "unitId": 1,
        }
    }

    with pytest.raises(ValueError, match="duplicate sensor unitId"):
        resolve_configuration(document)
