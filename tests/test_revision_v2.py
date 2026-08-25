from __future__ import annotations

import json
from datetime import timedelta, timezone
from pathlib import Path

from pi_node_supervisor.revision_v2 import RevisionStoreV2
from pi_node_supervisor.service_v2 import SupervisorServiceV2


def document():
    return {
        "schemaVersion": 2,
        "cameras": {
            "defaults": {"captureNode": {"images": {"dng": {}}}},
            "devices": {
                "rgb": {
                    "captureNode": {
                        "camera": {"type": "picamera2", "cameraNum": 0}
                    }
                }
            },
        },
        "power": {"minOffSeconds": 300},
    }


def test_revision_writes_source_resolved_and_generated_files(tmp_path: Path) -> None:
    service = SupervisorServiceV2(
        RevisionStoreV2(tmp_path / "state"),
        active_configuration_path=tmp_path / "etc/config.json",
        generated_capture_directory=tmp_path / "generated",
        power_configuration_path=tmp_path / "power/config.json",
        local_timezone=timezone(timedelta(hours=9)),
        timezone_name="Asia/Seoul",
    )

    revision = service.apply_document(document())

    assert revision["status"] == "active"
    assert service.active()["schemaVersion"] == 2
    generated = json.loads((tmp_path / "generated/rgb.json").read_text())
    assert generated["camera"]["cameraNum"] == 0
    revision_dir = tmp_path / "state/revisions/00000001"
    assert (revision_dir / "source.json").is_file()
    assert (revision_dir / "resolved.json").is_file()
    assert (revision_dir / "capture-nodes/rgb.json").is_file()


def test_failed_runtime_activation_restores_previous_revision(tmp_path: Path) -> None:
    service = SupervisorServiceV2(
        RevisionStoreV2(tmp_path / "state"),
        active_configuration_path=tmp_path / "etc/config.json",
        generated_capture_directory=tmp_path / "generated",
        power_configuration_path=tmp_path / "power/config.json",
        local_timezone=timezone.utc,
        timezone_name="UTC",
    )
    first = service.apply_document(document())
    changed = document()
    changed["cameras"]["devices"] = {}
    second = service.apply_document(changed)

    service.store.rollback_activation(
        first["id"],
        second["id"],
        active_configuration_path=service.active_configuration_path,
        generated_capture_directory=service.generated_capture_directory,
        power_configuration_path=service.power_configuration_path,
        error="CaptureNode rejected the generated document",
    )

    assert service.active()["cameras"]["devices"]["rgb"]
    assert (tmp_path / "generated/rgb.json").is_file()
    assert service.store.get_revision(second["id"])["status"] == "failed"
