from __future__ import annotations

import json
from pathlib import Path

from pi_node_supervisor.cli import main


def test_cli_validates_and_applies_v2_document(tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"schemaVersion": 2, "power": {"minOffSeconds": 300}}),
        encoding="utf-8",
    )
    common = [
        "--state-dir",
        str(tmp_path / "state"),
        "--active-config",
        str(tmp_path / "active.json"),
        "--generated-capture-dir",
        str(tmp_path / "capture-nodes"),
        "--power-config",
        str(tmp_path / "power.json"),
    ]

    assert main([*common, "validate-config", str(config)]) == 0
    capsys.readouterr()
    assert main([*common, "apply", str(config)]) == 0
    applied = json.loads(capsys.readouterr().out)

    assert applied["status"] == "active"
    assert json.loads((tmp_path / "active.json").read_text())["schemaVersion"] == 2


def test_cli_has_no_nodes_or_sync_data_contract() -> None:
    parser = __import__("pi_node_supervisor.cli", fromlist=["build_parser"]).build_parser()
    help_text = parser.format_help()

    assert "--nodes" not in help_text
    assert "sync-data" not in help_text
