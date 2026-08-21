#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/.." && pwd)
capture_source=${CAPTURE_NODE_SOURCE:-$project_root/components/capture-node}
target=${TARGET:-}
public_key=${PUBLIC_KEY:-$HOME/.ssh/id_ed25519.pub}
timezone=${TIMEZONE:-Asia/Seoul}
port=${PORT:-8080}
enable_power=false
nodes_file=${NODES_FILE:-$project_root/examples/nodes.dual-camera-sensor.json}
initial_config=${INITIAL_CONFIG:-$project_root/examples/config.empty.json}
huintech_device_source=${HUINTECHDEVICE_SOURCE:-$project_root/components/huintech-device/python}
power_manager_source=${POWER_MANAGER_SOURCE:-$project_root/components/pi-power-manager}
ftps_password_file=''
camera_specs=()

usage() {
  printf '%s\n' \
    "Usage: scripts/bootstrap-device.sh [options] USER@HOST" \
    "" \
    "Bootstraps SSH, Raspberry Pi 5 packages/RTC settings, CaptureNode," \
    "Supervisor, and pi-power-manager. Automatic shutdown is disabled by default." \
    "" \
    "  --public-key FILE       SSH public key (default: $public_key)" \
    "  --target USER@HOST      SSH destination (positional form is also accepted)" \
    "  --camera SPEC           picamera2[:N] or v4l2:/absolute/path; repeat for two" \
    "  --nodes FILE            Node registry, including optional sensor nodes" \
    "  --initial-config FILE   Initial Supervisor configuration" \
    "  --huintech-device-source PATH  HUINTECHDEVICE python source root" \
    "  --power-manager-source PATH    Local pi-power-manager source root" \
    "  --timezone ZONE         Device timezone (default: $timezone)" \
    "  --port PORT             Supervisor Web port (default: $port)" \
    "  --enable-power          Enable the timer; each boot's GPIO26 latch gates execution" \
    "  --ftps-password-file FILE  Install FTPS password from a local protected file" \
    "  -h, --help              Show this help"
}

while (($#)); do
  case "$1" in
    --public-key) public_key=${2:?missing value for --public-key}; shift 2 ;;
    --target) target=${2:?missing value for --target}; shift 2 ;;
    --camera) camera_specs+=("${2:?missing value for --camera}"); shift 2 ;;
    --nodes) nodes_file=${2:?missing value for --nodes}; shift 2 ;;
    --initial-config) initial_config=${2:?missing value for --initial-config}; shift 2 ;;
    --huintech-device-source) huintech_device_source=${2:?missing value for --huintech-device-source}; shift 2 ;;
    --power-manager-source) power_manager_source=${2:?missing value for --power-manager-source}; shift 2 ;;
    --timezone) timezone=${2:?missing value for --timezone}; shift 2 ;;
    --port) port=${2:?missing value for --port}; shift 2 ;;
    --enable-power) enable_power=true; shift ;;
    --ftps-password-file) ftps_password_file=${2:?missing value for --ftps-password-file}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    *) [[ -z "$target" ]] || { printf 'Only one target is allowed.\n' >&2; exit 2; }; target=$1; shift ;;
  esac
done

[[ "$target" =~ ^[A-Za-z_][A-Za-z0-9_-]*@[A-Za-z0-9._:-]+$ ]] || {
  printf 'Target must be USER@HOST.\n' >&2
  exit 2
}
[[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || {
  printf 'Port must be between 1 and 65535.\n' >&2
  exit 2
}
[[ "$timezone" =~ ^[A-Za-z0-9._+-]+(/[A-Za-z0-9._+-]+)+$ ]] || {
  printf 'Timezone must be an IANA name such as Asia/Seoul.\n' >&2
  exit 2
}
[[ -r "$capture_source/eng/deploy.sh" ]] || { printf 'CaptureNode source is unavailable. Set CAPTURE_NODE_SOURCE.\n' >&2; exit 2; }
[[ -r "$nodes_file" ]] || { printf 'Node registry is unavailable: %s\n' "$nodes_file" >&2; exit 2; }
[[ -r "$initial_config" ]] || { printf 'Initial configuration is unavailable: %s\n' "$initial_config" >&2; exit 2; }
[[ -z "$ftps_password_file" || -r "$ftps_password_file" ]] || { printf 'FTPS password file is unavailable: %s\n' "$ftps_password_file" >&2; exit 2; }
for command_name in scp ssh ssh-copy-id ssh-keygen; do
  command -v "$command_name" >/dev/null || { printf 'Missing local command: %s\n' "$command_name" >&2; exit 2; }
done

if [[ ! -r "$public_key" ]]; then
  private_key=${public_key%.pub}
  [[ "$public_key" == *.pub && ! -e "$private_key" ]] || {
    printf 'Public key is unavailable and cannot be generated safely: %s\n' "$public_key" >&2
    exit 2
  }
  install -d -m 700 "$(dirname -- "$private_key")"
  printf 'Generating SSH key: %s\n' "$private_key"
  ssh-keygen -q -t ed25519 -a 100 -N '' -f "$private_key"
fi

ssh_options=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
if ! ssh "${ssh_options[@]}" "$target" true; then
  [[ -t 0 ]] || { printf 'SSH key bootstrap requires an interactive terminal.\n' >&2; exit 1; }
  printf 'Installing SSH public key; the remote account password may be requested.\n'
  ssh-copy-id -o StrictHostKeyChecking=accept-new -i "$public_key" "$target"
  ssh "${ssh_options[@]}" "$target" true
fi

operator=${target%@*}
host=${target#*@}
remote_stage=$(ssh "${ssh_options[@]}" "$target" mktemp -d /tmp/pi-node-bootstrap.XXXXXX)
case "$remote_stage" in
  /tmp/pi-node-bootstrap.*) ;;
  *) printf 'Unexpected remote staging path.\n' >&2; exit 1 ;;
esac
cleanup() {
  ssh "${ssh_options[@]}" "$target" rm -rf -- "$remote_stage" >/dev/null 2>&1 || true
  [[ -z "${local_stage:-}" ]] || rm -rf -- "$local_stage"
}
trap cleanup EXIT INT TERM

scp -o ConnectTimeout=10 "$script_dir/bootstrap-device-remote.sh" "$target:$remote_stage/"
printf 'Installing Raspberry Pi prerequisites; sudo authentication may be requested.\n'
ssh -tt -o ConnectTimeout=10 "$target" \
  sudo bash "$remote_stage/bootstrap-device-remote.sh" "$operator" "$timezone"

printf 'Waiting for %s to reboot.\n' "$host"
for _attempt in $(seq 1 30); do
  if ! ssh "${ssh_options[@]}" "$target" true >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
for _attempt in $(seq 1 120); do
  if ssh "${ssh_options[@]}" "$target" true >/dev/null 2>&1; then
    break
  fi
  if ((_attempt == 120)); then
    printf 'Device did not return after reboot.\n' >&2
    exit 1
  fi
  sleep 2
done

if ((${#camera_specs[@]} == 0)); then
  camera_specs=(picamera2:0 picamera2:1)
fi
(( ${#camera_specs[@]} >= 1 && ${#camera_specs[@]} <= 2 )) || {
  printf 'One or two cameras are supported.\n' >&2
  exit 2
}
capture_arguments=()
for camera_spec in "${camera_specs[@]}"; do
  capture_arguments+=(--camera "$camera_spec")
done
"$capture_source/eng/deploy.sh" \
  "${capture_arguments[@]}" \
  --skip-tests \
  --skip-linger \
  "$target"

local_stage=$(mktemp -d /tmp/pi-node-bootstrap-local.XXXXXX)
source_nodes_file=$nodes_file
rendered_nodes_file="$local_stage/nodes.json"
python3 - "$source_nodes_file" "$rendered_nodes_file" "$operator" "${#camera_specs[@]}" <<'PYTHON'
import json
from pathlib import Path
import sys

source, destination = map(Path, sys.argv[1:3])
operator = sys.argv[3]
camera_count = int(sys.argv[4])
document = json.loads(source.read_text(encoding="utf-8"))
camera_nodes = [node for node in document["nodes"] if node.get("managed_capture")]
other_nodes = [node for node in document["nodes"] if not node.get("managed_capture")]
if len(camera_nodes) < camera_count:
    raise SystemExit(
        f"node registry defines {len(camera_nodes)} managed cameras, but {camera_count} were requested"
    )
document["nodes"] = camera_nodes[:camera_count] + other_nodes
old_home = "/home/huintech"
new_home = f"/home/{operator}"
for node in document["nodes"]:
    managed = node.get("managed_capture")
    if managed:
        for key in ("config_path", "working_directory"):
            managed[key] = managed[key].replace(old_home, new_home, 1)
destination.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
PYTHON

nodes_file=$rendered_nodes_file

remote_base="/home/$operator/pi-node-supervisor"
capture_executable="/home/$operator/capture-node/.venv/bin/capture-node-web"
supervisor_arguments=(
  --target "$target" \
  --remote-base "$remote_base" \
  --nodes "$nodes_file" \
  --capture-node-executable "$capture_executable" \
  --initial-config "$initial_config" \
  --bind-host 0.0.0.0 \
  --port "$port"
)
supervisor_arguments+=(--huintech-device-source "$huintech_device_source")
supervisor_arguments+=(--power-manager-source "$power_manager_source")
if [[ "$enable_power" == true ]]; then
  supervisor_arguments+=(--enable-power)
fi
if [[ -n "$ftps_password_file" ]]; then
  supervisor_arguments+=(--ftps-password-file "$ftps_password_file")
fi
"$script_dir/deploy.sh" "${supervisor_arguments[@]}"

printf 'Verifying deployment.\n'
ssh "${ssh_options[@]}" "$target" \
  "systemctl --user is-active pi-node-supervisor.service && \
   curl -fsS http://127.0.0.1:$port/api/health && \
   printf '\\ngpio26=' && \
   { cat /run/pi-power-manager/gpio26-mode 2>/dev/null || printf 'not-latched'; }"
printf '\nBootstrap complete: http://%s:%s/ power_enabled=%s\n' "$host" "$port" "$enable_power"
