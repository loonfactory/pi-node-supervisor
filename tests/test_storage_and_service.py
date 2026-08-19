from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pi_node_supervisor.errors import ApplyError, PersistenceError
from pi_node_supervisor.models import SupervisorConfiguration
from pi_node_supervisor.service import SupervisorService
from pi_node_supervisor.storage import RevisionStore


def document(at="2030-01-01T08:00:00"):
    return {
        "schema_version": 1,
        "timezone": "Asia/Seoul",
        "policies": {
            "boot-lead-seconds": 120,
            "shutdown-grace-seconds": 60,
            "merge-power-gaps-shorter-than-seconds": 1800,
        },
        "activities": [
            {
                "id": "capture",
                "type": "capture",
                "at": at,
                "node": "camera0",
                "profile": "default",
            }
        ],
    }


def normalized_document(value=None):
    return SupervisorConfiguration.model_validate(value or document()).model_dump(
        mode="json", by_alias=True
    )


def test_draft_round_trip_is_validated_and_atomic(service: SupervisorService):
    plan = service.save_draft(document())
    assert service.draft() == normalized_document()
    assert service.store.draft_path.stat().st_mode & 0o777 == 0o600
    assert plan.source_hash


def test_apply_creates_immutable_artifacts_and_strict_external_files(
    service: SupervisorService,
):
    revision = service.apply_document(document())
    assert revision["id"] == 1
    assert revision["status"] == "active"
    assert service.store.status()["active_revision"]["id"] == 1
    revision_dir = service.store.revisions_directory / "00000001"
    assert {path.name for path in revision_dir.iterdir()} == {
        "config.json",
        "plan.json",
        "pi-power-manager.json",
    }
    power = json.loads(service.power_manager_configuration_path.read_text())
    assert set(power["power_schedule"][0]) == {"power-on", "power-down"}
    assert service.active() == normalized_document()


def test_second_apply_supersedes_previous_revision(service: SupervisorService):
    service.apply_document(document())
    second = service.apply_document(document("2030-01-02T08:00:00"))
    assert second["id"] == 2
    assert [item["status"] for item in service.store.list_revisions()] == [
        "active",
        "superseded",
    ]


def test_recurring_power_plan_is_refreshed_on_a_rolling_horizon(service: SupervisorService):
    recurring = {
        "schema_version": 1,
        "timezone": "UTC",
        "activities": [],
        "capture_schedules": [
            {
                "id": "camera0-daily",
                "node": "camera0",
                "profile": "default",
                "mode": "daily",
                "interval_seconds": None,
                "daily_times": ["08:00:00"],
                "timezone": "UTC",
            }
        ],
    }
    service.apply_document(recurring)
    service.refresh_recurring_power(datetime(2030, 1, 1, tzinfo=UTC))

    refreshed = json.loads(service.power_manager_configuration_path.read_text())
    assert refreshed["power_schedule"][0]["power-on"].startswith("2030-01-")
    assert len(refreshed["power_schedule"]) >= 7


def test_failed_activation_restores_power_file_and_keeps_no_active_revision(
    tmp_path: Path, registry
):
    state = RevisionStore(tmp_path / "state")
    power_path = tmp_path / "power.json"
    power_path.write_text('{"old": true}\n', encoding="utf-8")
    impossible_parent = tmp_path / "not-a-directory"
    impossible_parent.write_text("blocking file", encoding="utf-8")
    service = SupervisorService(
        registry,
        state,
        active_configuration_path=impossible_parent / "active.json",
        power_manager_configuration_path=power_path,
    )

    with pytest.raises(ApplyError, match="was not activated"):
        service.apply_document(document())

    assert power_path.read_text(encoding="utf-8") == '{"old": true}\n'
    assert state.status()["active_revision"] is None
    assert state.list_revisions()[0]["status"] == "failed"


def test_load_missing_draft_is_an_operator_error(tmp_path: Path):
    with pytest.raises(PersistenceError, match="no draft"):
        RevisionStore(tmp_path / "state").load_draft()
