#!/usr/bin/env bash
set -Eeuo pipefail

operator=${1:?operator user is required}
timezone=${2:-Asia/Seoul}

[[ $(id -u) -eq 0 ]] || { printf 'Run through sudo.\n' >&2; exit 2; }
[[ "$operator" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || { printf 'Invalid operator user.\n' >&2; exit 2; }
id "$operator" >/dev/null

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  curl \
  python3 \
  python3-pip \
  python3-venv \
  python3-picamera2 \
  rpicam-apps \
  raspi-utils \
  rpi-eeprom \
  rsync

for command_name in curl loginctl pinctrl python3 rpi-eeprom-config rsync systemctl; do
  command -v "$command_name" >/dev/null || {
    printf 'Required command is unavailable after package installation: %s\n' "$command_name" >&2
    exit 1
  }
done

model=$(tr -d '\000' </proc/device-tree/model)
case "$model" in
  *"Raspberry Pi 5"*) ;;
  *) printf 'Unsupported hardware: %s\n' "$model" >&2; exit 1 ;;
esac

timedatectl set-timezone "$timezone"
systemctl enable ssh
loginctl enable-linger "$operator"
usermod -aG dialout "$operator"

temporary_dir=$(mktemp -d /tmp/pi-node-bootstrap.XXXXXX)
cleanup() {
  rm -rf -- "$temporary_dir"
}
trap cleanup EXIT

current_config="$temporary_dir/boot-current.conf"
desired_config="$temporary_dir/boot-desired.conf"
rpi-eeprom-config >"$current_config"
python3 - "$current_config" "$desired_config" <<'PYTHON'
from pathlib import Path
import sys

source, destination = map(Path, sys.argv[1:])
expected = {"POWER_OFF_ON_HALT": "1", "WAKE_ON_GPIO": "0"}
lines = source.read_text(encoding="utf-8").splitlines()
seen: set[str] = set()
result: list[str] = []
for line in lines:
    stripped = line.strip()
    if "=" in stripped and not stripped.startswith("#"):
        key = stripped.split("=", 1)[0].strip()
        if key in expected:
            result.append(f"{key}={expected[key]}")
            seen.add(key)
            continue
    result.append(line)
for key, value in expected.items():
    if key not in seen:
        result.append(f"{key}={value}")
destination.write_text("\n".join(result) + "\n", encoding="utf-8")
PYTHON

if ! cmp -s "$current_config" "$desired_config"; then
  rpi-eeprom-config --apply "$desired_config"
  printf 'Bootloader RTC power settings updated.\n'
else
  printf 'Bootloader RTC power settings already match.\n'
fi

printf 'hardware=%s operator=%s timezone=%s\n' "$model" "$operator" "$timezone"
systemd-run --quiet --unit="pi-node-bootstrap-reboot-$$" --on-active=2s \
  /usr/bin/systemctl reboot
printf 'Reboot scheduled.\n'
