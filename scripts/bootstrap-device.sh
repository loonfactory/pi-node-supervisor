#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/.." && pwd)
capture_source=${CAPTURE_NODE_SOURCE:-$project_root/components/capture-node}
target=${TARGET:-}
public_key=${PUBLIC_KEY:-$HOME/.ssh/id_ed25519.pub}
timezone=${TIMEZONE:-Asia/Seoul}
port=${PORT:-8080}
config_file=${CONFIG_FILE:-$project_root/examples/config.json}
camera_specs=()

usage() {
  printf '%s\n' \
    "Usage: scripts/bootstrap-device.sh [options] USER@HOST" \
    "  --public-key FILE" \
    "  --target USER@HOST" \
    "  --camera picamera2:N     repeat for each camera" \
    "  --config FILE            Supervisor v2 config" \
    "  --timezone ZONE" \
    "  --port PORT"
}
while (($#)); do
  case "$1" in
    --public-key) public_key=${2:?missing value}; shift 2 ;;
    --target) target=${2:?missing value}; shift 2 ;;
    --camera) camera_specs+=("${2:?missing value}"); shift 2 ;;
    --config|--initial-config) config_file=${2:?missing value}; shift 2 ;;
    --timezone) timezone=${2:?missing value}; shift 2 ;;
    --port) port=${2:?missing value}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    *) [[ -z "$target" ]] || { printf 'Only one target is allowed\n' >&2; exit 2; }; target=$1; shift ;;
  esac
done
[[ "$target" =~ ^[A-Za-z_][A-Za-z0-9_-]*@[A-Za-z0-9._:-]+$ ]] || { printf 'Target must be USER@HOST\n' >&2; exit 2; }
[[ -r "$config_file" && -x "$capture_source/eng/deploy.sh" ]] || { printf 'Source or config unavailable\n' >&2; exit 2; }

if [[ ! -r "$public_key" ]]; then
  private_key=${public_key%.pub}
  ssh-keygen -q -t ed25519 -a 100 -N '' -f "$private_key"
fi
ssh_options=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
if ! ssh "${ssh_options[@]}" "$target" true; then
  ssh-copy-id -o StrictHostKeyChecking=accept-new -i "$public_key" "$target"
fi
operator=${target%@*}
remote_stage=$(ssh "${ssh_options[@]}" "$target" mktemp -d /tmp/pi-node-bootstrap.XXXXXX)
cleanup() { ssh "${ssh_options[@]}" "$target" rm -rf -- "$remote_stage" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
scp -o ConnectTimeout=10 "$script_dir/bootstrap-device-remote.sh" "$target:$remote_stage/"
ssh -tt -o ConnectTimeout=10 "$target" sudo bash "$remote_stage/bootstrap-device-remote.sh" "$operator" "$timezone"

for _ in $(seq 1 120); do ssh "${ssh_options[@]}" "$target" true >/dev/null 2>&1 && break; sleep 2; done
((${#camera_specs[@]})) || camera_specs=(picamera2:0 picamera2:1)
capture_arguments=()
for camera in "${camera_specs[@]}"; do capture_arguments+=(--camera "$camera"); done
"$capture_source/eng/deploy.sh" "${capture_arguments[@]}" --skip-tests --skip-linger "$target"

"$script_dir/deploy.sh" --target "$target" \
  --remote-base "/home/$operator/pi-node-supervisor" \
  --config "$config_file" \
  --capture-node-executable "/home/$operator/capture-node/.venv/bin/capture-node-web" \
  --bind-host 0.0.0.0 --port "$port"
