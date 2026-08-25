#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/.." && pwd)
target=${TARGET:-huintech@192.168.12.7}
remote_base=${REMOTE_BASE:-/home/huintech/pi-node-supervisor}
config_file=${CONFIG_FILE:-$project_root/examples/config.json}
capture_executable=${CAPTURE_NODE_EXECUTABLE:-/home/huintech/capture-node/.venv/bin/capture-node-web}
power_executable=${POWER_MANAGER_EXECUTABLE:-/opt/pi-power-manager/current/.venv/bin/pi-power-manager}
power_config=${POWER_CONFIG:-}
power_state=${POWER_STATE:-}
bind_host=${BIND_HOST:-0.0.0.0}
port=${PORT:-8080}
replace_config=false

usage() {
  printf '%s\n' \
    "Usage: scripts/deploy.sh [options]" \
    "  --target USER@HOST" \
    "  --remote-base PATH" \
    "  --config FILE             v2 source config" \
    "  --replace-config          apply FILE even when an active config exists" \
    "  --capture-node-executable PATH" \
    "  --power-manager-executable PATH" \
    "  --power-config PATH" \
    "  --power-state PATH" \
    "  --bind-host ADDRESS" \
    "  --port PORT"
}

while (($#)); do
  case "$1" in
    --target) target=${2:?missing value}; shift 2 ;;
    --remote-base) remote_base=${2:?missing value}; shift 2 ;;
    --config|--initial-config) config_file=${2:?missing value}; shift 2 ;;
    --replace-config|--update-settings) replace_config=true; shift ;;
    --capture-node-executable) capture_executable=${2:?missing value}; shift 2 ;;
    --power-manager-executable) power_executable=${2:?missing value}; shift 2 ;;
    --power-config) power_config=${2:?missing value}; shift 2 ;;
    --power-state) power_state=${2:?missing value}; shift 2 ;;
    --bind-host) bind_host=${2:?missing value}; shift 2 ;;
    --port) port=${2:?missing value}; shift 2 ;;
    --without-power-manager) shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

power_config=${power_config:-$remote_base/generated/pi-power-manager.json}
power_state=${power_state:-$remote_base/state/pi-power-manager.json}

[[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ ]] || { printf 'Invalid target\n' >&2; exit 2; }
[[ "$remote_base" == /* && "$remote_base" != / ]] || { printf 'Invalid remote base\n' >&2; exit 2; }
[[ -r "$config_file" ]] || { printf 'Config is unreadable: %s\n' "$config_file" >&2; exit 2; }
[[ "$port" =~ ^[0-9]+$ ]] && ((port > 0 && port < 65536)) || { printf 'Invalid port\n' >&2; exit 2; }

PYTHONPATH="$project_root/src" python3 -m pi_node_supervisor.cli validate-config "$config_file"
frontend="$project_root/src/pi_node_supervisor/web/frontend"
(cd "$frontend" && npm run build)

build_dir=$(mktemp -d /tmp/pi-node-supervisor-build.XXXXXX)
remote_stage=''
cleanup() {
  code=$?
  [[ -z "$remote_stage" ]] || ssh -o ConnectTimeout=10 "$target" rm -rf -- "$remote_stage" >/dev/null 2>&1 || true
  rm -rf -- "$build_dir"
  exit "$code"
}
trap cleanup EXIT INT TERM

if python3 -c 'import build' >/dev/null 2>&1; then
  python3 -m build --wheel --outdir "$build_dir" "$project_root"
else
  python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir "$build_dir" "$project_root"
fi
wheel=$(find "$build_dir" -maxdepth 1 -name 'pi_node_supervisor-*.whl' -print -quit)
[[ -n "$wheel" ]] || { printf 'Wheel build failed\n' >&2; exit 1; }
release_id="$(date -u +%Y%m%dT%H%M%SZ)-$(sha256sum "$wheel" | cut -c1-12)"
remote_stage=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" mktemp -d /tmp/pi-node-supervisor-deploy.XXXXXX)
scp -o ConnectTimeout=10 "$wheel" "$config_file" "$project_root/systemd/pi-node-supervisor.user.service.in" "$target:$remote_stage/"

ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" bash -s -- \
  "$remote_base" "$remote_stage" "$release_id" "$capture_executable" \
  "$power_executable" "$power_config" "$power_state" "$bind_host" "$port" "$replace_config" <<'REMOTE'
set -Eeuo pipefail
base=$1; stage=$2; release_id=$3; capture=$4; power=$5; power_config=$6
power_state=$7; bind_host=$8; port=$9; replace_config=${10}
case "$base" in "$HOME"/*) ;; *) printf 'Install root must be below HOME\n' >&2; exit 2;; esac
wheel=$(find "$stage" -maxdepth 1 -name 'pi_node_supervisor-*.whl' -print -quit)
config=$(find "$stage" -maxdepth 1 -name '*.json' -print -quit)
template="$stage/pi-node-supervisor.user.service.in"
release="$base/releases/$release_id"
install -d -m 700 "$release" "$base/config" "$base/state" "$base/generated/capture-nodes" "$HOME/.config/systemd/user"
python3 -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install "$wheel[web,sensor,remote]"
"$release/.venv/bin/pi-node-supervisor" validate-config "$config"
if [[ ! -s "$base/config/supervisor.env" ]]; then
  umask 077
  "$release/.venv/bin/python" -c 'import secrets; print("PI_NODE_SUPERVISOR_API_TOKEN=" + secrets.token_urlsafe(32))' > "$base/config/supervisor.env"
fi
first=false
[[ -f "$base/config/config.json" ]] || first=true
if [[ "$first" == true || "$replace_config" == true ]]; then
  "$release/.venv/bin/pi-node-supervisor" \
    --state-dir "$base/state" --active-config "$base/config/config.json" \
    --generated-capture-dir "$base/generated/capture-nodes" \
    --power-config "$power_config" apply "$config"
fi
ln -sfn "$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
power_argument=''
if [[ -x "$power" ]]; then
  power_argument="--power-manager-executable $power"
fi
sed -e "s|@BIND_HOST@|$bind_host|g" -e "s|@PORT@|$port|g" \
  -e "s|@CAPTURE_NODE_EXECUTABLE@|$capture|g" \
  -e "s|@POWER_MANAGER_ARGUMENT@|$power_argument|g" \
  -e "s|@POWER_CONFIG@|$power_config|g" -e "s|@POWER_STATE@|$power_state|g" \
  "$template" > "$HOME/.config/systemd/user/pi-node-supervisor.service"
systemctl --user daemon-reload
systemctl --user enable --now pi-node-supervisor.service
for _ in $(seq 1 120); do
  curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$port/api/health"
REMOTE

printf '\nDeployment complete: http://%s:%s/\n' "${target#*@}" "$port"
