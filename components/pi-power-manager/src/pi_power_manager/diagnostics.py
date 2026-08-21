from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from .rtc import RTC


def diagnose(rtc: RTC, config_path: Path, state_path: Path) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    model_path = Path("/proc/device-tree/model")
    is_pi5 = False
    if model_path.exists():
        try:
            model = model_path.read_text(encoding="ascii").rstrip("\x00\n")
            status = "pass" if "Raspberry Pi 5" in model else "fail"
            is_pi5 = status == "pass"
            checks.append({"name": "hardware", "status": status, "detail": model})
        except OSError as exc:
            checks.append({"name": "hardware", "status": "fail", "detail": str(exc)})
    else:
        checks.append({
            "name": "hardware", "status": "warn",
            "detail": f"model unavailable on {platform.system()}; HIL check required",
        })
    if is_pi5:
        checks.append(_bootloader_check())
    else:
        checks.append({
            "name": "bootloader", "status": "warn",
            "detail": "bootloader settings require Raspberry Pi 5 HIL verification",
        })
    checks.append({
        "name": "systemd",
        "status": "pass" if shutil.which("systemctl") else "fail",
        "detail": shutil.which("systemctl") or "systemctl not found",
    })
    checks.extend(rtc.check())
    storage_paths = (
        ("config_storage", config_path, os.R_OK, False),
        ("state_storage", state_path, os.R_OK | os.W_OK, True),
    )
    for name, path, access_mode, may_create in storage_paths:
        parent = path.parent
        accessible = may_create and parent.exists() and os.access(parent, os.W_OK)
        if path.exists():
            accessible = os.access(path, access_mode)
        checks.append({
            "name": name,
            "status": "pass" if accessible else "fail",
            "detail": str(path),
        })
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
            checks.append({
                "name": f"{name}_permissions",
                "status": "pass" if mode & 0o022 == 0 else "fail",
                "detail": f"{path} mode={mode:04o}",
            })
    ready = all(item["status"] == "pass" for item in checks)
    return {"automatic_shutdown_ready": ready, "checks": checks}


def _bootloader_check() -> dict[str, str]:
    executable = shutil.which("rpi-eeprom-config")
    if executable is None:
        return {
            "name": "bootloader", "status": "fail", "detail": "rpi-eeprom-config not found"
        }
    try:
        result = subprocess.run(
            [executable], check=True, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"name": "bootloader", "status": "fail", "detail": str(exc)}
    settings: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip()
    expected = {"POWER_OFF_ON_HALT": "1", "WAKE_ON_GPIO": "0"}
    mismatches = [
        f"{key}={settings.get(key, '<missing>')}"
        for key, value in expected.items()
        if settings.get(key) != value
    ]
    if mismatches:
        return {"name": "bootloader", "status": "fail", "detail": ", ".join(mismatches)}
    return {"name": "bootloader", "status": "pass", "detail": "RTC wake settings present"}
