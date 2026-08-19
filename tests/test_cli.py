from __future__ import annotations

import json
from pathlib import Path

from pi_node_supervisor.cli import main

from test_storage_and_service import document


def write_inputs(tmp_path: Path):
    nodes = tmp_path / "nodes.json"
    nodes.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "nodes": [
                    {
                        "id": "camera0",
                        "kind": "capture-node",
                        "activity_types": ["capture"],
                        "profiles": ["default"],
                        "default_runtime_seconds": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(json.dumps(document()), encoding="utf-8")
    return nodes, config


def common_args(tmp_path: Path, nodes: Path):
    return [
        "--nodes",
        str(nodes),
        "--state-dir",
        str(tmp_path / "state"),
        "--active-config",
        str(tmp_path / "active.json"),
        "--power-config",
        str(tmp_path / "power.json"),
    ]


def test_cli_validates_and_renders_power_contract(tmp_path: Path, capsys):
    nodes, config = write_inputs(tmp_path)
    args = common_args(tmp_path, nodes)
    assert main([*args, "validate-config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    assert main([*args, "plan", str(config), "--power-manager"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 2
    assert set(result["power_schedule"][0]) == {"power-on", "power-down"}


def test_cli_apply_and_status(tmp_path: Path, capsys):
    nodes, config = write_inputs(tmp_path)
    args = common_args(tmp_path, nodes)
    assert main([*args, "apply", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "active"
    assert main([*args, "status"]) == 0
    assert json.loads(capsys.readouterr().out)["active_revision"]["id"] == 1


def test_cli_reports_invalid_configuration(tmp_path: Path, capsys):
    nodes, config = write_inputs(tmp_path)
    bad = document()
    bad["activities"][0]["profile"] = "not-installed"
    config.write_text(json.dumps(bad), encoding="utf-8")
    assert main([*common_args(tmp_path, nodes), "validate-config", str(config)]) == 2
    assert "does not provide profile" in capsys.readouterr().err

