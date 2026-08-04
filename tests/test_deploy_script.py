from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPOSITORY_ROOT / "eng" / "deploy.sh"


def run_deploy(*arguments: str, path: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if path is not None:
        environment["PATH"] = f"{path}:{environment['PATH']}"
    return subprocess.run(
        [str(DEPLOY_SCRIPT), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def fake_deployment_commands(directory: Path) -> Path:
    directory.mkdir()
    for name in ("npm", "rsync", "ssh"):
        command = directory / name
        command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        command.chmod(0o755)
    return directory


def test_deploy_script_has_valid_bash_syntax() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_deploy_script_prints_explicit_camera_plan(tmp_path: Path) -> None:
    commands = fake_deployment_commands(tmp_path / "bin")

    completed = run_deploy(
        "--skip-tests",
        "--dry-run",
        "--offline",
        "--camera",
        "v4l2:/dev/video0",
        "--camera",
        "picamera2:1",
        "operator@capture-node.local",
        path=commands,
    )

    assert completed.returncode == 0, completed.stderr
    assert "camera0 | port 8000 | initial camera v4l2:/dev/video0" in completed.stdout
    assert "camera1 | port 8001 | initial camera picamera2:1" in completed.stdout
    assert "Packages: offline" in completed.stdout


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ("--camera-count", "2", "--camera", "picamera2", "user@host"),
            "--camera and --camera-count cannot be used together",
        ),
        (("--camera", "usb:/dev/video0", "user@host"), "invalid --camera value"),
        (("--camera-count", "3", "user@host"), "one or two cameras are supported"),
        (("--camera-count", "invalid", "user@host"), "one or two cameras are supported"),
        (
            ("--offline", "--cache-wheels", "user@host"),
            "--cache-wheels requires an online deployment",
        ),
    ],
)
def test_deploy_script_rejects_ambiguous_options(
    arguments: tuple[str, ...],
    message: str,
) -> None:
    completed = run_deploy(*arguments)

    assert completed.returncode != 0
    assert message in completed.stderr
