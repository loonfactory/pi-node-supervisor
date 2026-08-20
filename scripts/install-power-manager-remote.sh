#!/usr/bin/env bash
set -Eeuo pipefail

operator=$1
supervisor_base=$2
stage=$3
release_id=$4
enable_timer=${5:-false}

[[ $(id -u) -eq 0 ]] || { printf 'Run this installer through sudo.\n' >&2; exit 2; }
[[ "$operator" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || { printf 'Invalid operator user.\n' >&2; exit 2; }
case "$supervisor_base" in
  "/home/$operator"/*) ;;
  *) printf 'Supervisor base must be below the operator home.\n' >&2; exit 2 ;;
esac
case "$stage" in
  /tmp/pi-power-manager-deploy.*) ;;
  *) printf 'Invalid staging path.\n' >&2; exit 2 ;;
esac
[[ "$enable_timer" == true || "$enable_timer" == false ]] || { printf 'Invalid timer state.\n' >&2; exit 2; }

wheel=$(find "$stage" -maxdepth 1 -type f -name 'pi_power_manager-*.whl' -print -quit)
[[ -r "$wheel" ]] || { printf 'pi-power-manager wheel is missing.\n' >&2; exit 1; }

release="/opt/pi-power-manager/releases/$release_id"
executable="$release/.venv/bin/pi-power-manager"
config_dir=/etc/pi-power-manager
config_path="$config_dir/config.json"
state_dir=/var/lib/pi-power-manager
state_path="$state_dir/state.json"
source_config="$supervisor_base/generated/pi-power-manager.json"

[[ -r "$source_config" ]] || {
  printf 'Supervisor has not generated a power configuration: %s\n' "$source_config" >&2
  exit 1
}

install -d -m 0755 /opt/pi-power-manager /opt/pi-power-manager/releases "$release"
python3 -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install --no-deps "$wheel"
ln -sfn "$release" /opt/pi-power-manager/current.next
mv -Tf /opt/pi-power-manager/current.next /opt/pi-power-manager/current

# Supervisor owns only this dedicated configuration directory. The privileged
# service owns RTC access, runtime state, and shutdown requests.
install -d -o "$operator" -g "$operator" -m 0750 "$config_dir"
install -o "$operator" -g "$operator" -m 0640 "$source_config" "$config_path"
install -d -o root -g root -m 0750 "$state_dir"

install -d -o root -g root -m 0755 /usr/local/libexec
install -m 0755 /dev/stdin /usr/local/libexec/pi-power-manager-gpio26-gate <<'GATE'
#!/bin/sh
set -eu

# Physical pin 37 / BCM GPIO26 is sampled once when the interlock service starts.
# The result is latched under /run for the rest of this boot. LOW (jumpered to
# GND) blocks automatic power management; HIGH (open, internal pull-up) permits
# it. Any setup/read failure latches the fail-safe blocked state.
pinctrl=/usr/bin/pinctrl
latch_dir=/run/pi-power-manager
latch=$latch_dir/gpio26-mode

write_latch() {
  install -d -o root -g root -m 0755 "$latch_dir"
  temporary="$latch.tmp.$$"
  printf '%s\n' "$1" >"$temporary"
  chmod 0644 "$temporary"
  mv -f "$temporary" "$latch"
}

case "${1:-check}" in
  latch)
    if ! "$pinctrl" set 26 ip pu; then
      write_latch blocked
      logger -t pi-power-manager "GPIO26 startup interlock setup failed; this boot is blocked"
      exit 0
    fi
    state=$("$pinctrl" get 26) || {
      write_latch blocked
      logger -t pi-power-manager "GPIO26 startup interlock read failed; this boot is blocked"
      exit 0
    }
    case "$state" in
      *"| hi "*) mode=enabled ;;
      *"| lo "*) mode=blocked ;;
      *) mode=blocked ;;
    esac
    write_latch "$mode"
    logger -t pi-power-manager "GPIO26 startup interlock latched: $mode"
    ;;
  check)
    test -r "$latch" && test "$(cat "$latch")" = enabled
    ;;
  *) exit 2 ;;
esac
GATE

install -m 0644 /dev/stdin /etc/systemd/system/pi-power-manager-interlock.service <<'INTERLOCK'
[Unit]
Description=Latch GPIO26 automatic power mode at startup
After=local-fs.target
Before=pi-power-manager.timer pi-power-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/pi-power-manager-gpio26-gate latch
RemainAfterExit=yes
User=root
Group=root

[Install]
WantedBy=timers.target
INTERLOCK

install -m 0644 /dev/stdin /etc/systemd/system/pi-power-manager.service <<UNIT
[Unit]
Description=pi-power-manager RTC wake alarm scheduler
After=local-fs.target pi-power-manager-interlock.service
Wants=pi-power-manager-interlock.service

[Service]
Type=oneshot
ExecCondition=/usr/local/libexec/pi-power-manager-gpio26-gate check
ExecStart=/opt/pi-power-manager/current/.venv/bin/pi-power-manager --config=$config_path --state=$state_path --rtc=/sys/class/rtc/rtc0 tick --execute --json
User=root
Group=root
StateDirectory=pi-power-manager
StateDirectoryMode=0750
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=$config_dir
ReadWritePaths=$state_dir /sys/class/rtc/rtc0/wakealarm
UNIT

install -m 0644 /dev/stdin /etc/systemd/system/pi-power-manager.timer <<'TIMER'
[Unit]
Description=Evaluate pi-power-manager schedules
After=pi-power-manager-interlock.service
Wants=pi-power-manager-interlock.service

[Timer]
OnBootSec=30s
OnUnitActiveSec=30s
AccuracySec=1s
Unit=pi-power-manager.service

[Install]
WantedBy=timers.target
TIMER

systemctl daemon-reload
"$executable" --config "$config_path" --state "$state_path" validate-config >/dev/null

if [[ "$enable_timer" == true ]]; then
  diagnostics=$(
    "$executable" --config "$config_path" --state "$state_path" \
      --rtc /sys/class/rtc/rtc0 diagnose --json
  ) || {
    printf '%s\n' "$diagnostics" >&2
    printf 'Diagnostics failed; timer remains disabled.\n' >&2
    systemctl disable --now pi-power-manager.timer >/dev/null 2>&1 || true
    exit 1
  }
  systemctl enable pi-power-manager-interlock.service pi-power-manager.timer
  systemctl restart pi-power-manager-interlock.service
  systemctl start pi-power-manager.timer
  latched_mode=$(cat /run/pi-power-manager/gpio26-mode 2>/dev/null || printf 'blocked')
  if [[ "$latched_mode" != enabled ]]; then
    systemctl disable --now pi-power-manager.timer >/dev/null 2>&1 || true
    printf 'GPIO26 startup interlock is %s; timer was disabled. Remove the jumper and rerun with --enable.\n' "$latched_mode" >&2
    exit 1
  fi
  printf 'pi-power-manager installed; timer enabled; startup mode=%s.\n' "$latched_mode"
else
  systemctl disable --now pi-power-manager.timer >/dev/null 2>&1 || true
  systemctl disable --now pi-power-manager-interlock.service >/dev/null 2>&1 || true
  printf 'pi-power-manager installed; timer is disabled and inactive.\n'
fi
