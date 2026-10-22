from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPOSITORY_ROOT / "eng" / "deploy.sh"


def run_deploy(
    *arguments: str,
    path: Path | None = None,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if path is not None:
        environment["PATH"] = f"{path}:{environment['PATH']}"
    environment.update(environment_overrides or {})
    return subprocess.run(
        [str(DEPLOY_SCRIPT), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def fake_deployment_commands(directory: Path, *, log: Path | None = None) -> Path:
    directory.mkdir()
    for name in ("npm", "rsync", "ssh"):
        command = directory / name
        command.write_text(
            "#!/bin/sh\n"
            + (
                f"printf '%s\\n' \"$(basename \"$0\")\" >> {log}\n"
                if log is not None
                else ""
            )
            + "case \"$(basename \"$0\"):$*\" in\n"
            + "  ssh:*deployed-source.sha256*) "
            + "printf '%s\\n' \"${FAKE_DEPLOYED_SOURCE_ID:-}\" ;;\n"
            + "esac\n"
            + "exit 0\n",
            encoding="utf-8",
        )
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


def test_deploy_skips_all_build_and_transfer_work_when_source_is_current(
    tmp_path: Path,
) -> None:
    command_log = tmp_path / "commands.log"
    commands = fake_deployment_commands(tmp_path / "bin", log=command_log)
    dry_run = run_deploy(
        "--skip-tests",
        "--dry-run",
        "--supervisor-managed",
        "operator@capture-node.local",
        path=commands,
    )
    source_id_match = re.search(r"Source ID: ([0-9a-f]{64})", dry_run.stdout)
    assert dry_run.returncode == 0, dry_run.stderr
    assert source_id_match is not None
    command_log.unlink()

    completed = run_deploy(
        "--skip-tests",
        "--skip-if-current",
        "--supervisor-managed",
        "operator@capture-node.local",
        path=commands,
        environment_overrides={
            "FAKE_DEPLOYED_SOURCE_ID": source_id_match.group(1),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert "CaptureNode is already current; skipping deployment" in completed.stdout
    assert command_log.read_text(encoding="utf-8").splitlines() == ["ssh", "ssh"]


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
