from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_power_install_scripts_have_valid_shell_syntax() -> None:
    for name in (
        "deploy.sh",
        "install-power-manager.sh",
        "install-power-manager-remote.sh",
    ):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / name)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_root_power_service_uses_supervisor_hold_interlock() -> None:
    installer = (ROOT / "scripts/install-power-manager-remote.sh").read_text()

    assert "ExecCondition=/usr/bin/test ! -e $hold_path" in installer
    assert "ExecStartPre=/usr/bin/touch $hold_path" in installer
    assert "install -o \"$operator\" -g \"$operator\" -m 0600 /dev/null \"$hold_path\"" in installer
