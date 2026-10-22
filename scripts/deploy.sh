#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/.." && pwd)
target=${TARGET:-huintech@192.168.12.7}
remote_base=${REMOTE_BASE:-}
config_file=${CONFIG_FILE:-$project_root/examples/config.json}
capture_source=${CAPTURE_NODE_SOURCE:-$project_root/components/capture-node}
capture_executable=${CAPTURE_NODE_EXECUTABLE:-}
huintech_device_source=${HUINTECH_DEVICE_SOURCE:-$project_root/components/huintech-device/python}
power_executable=${POWER_MANAGER_EXECUTABLE:-/opt/pi-power-manager/current/.venv/bin/pi-power-manager}
power_config=${POWER_CONFIG:-}
power_state=${POWER_STATE:-}
power_hold=${POWER_HOLD:-}
local_storage_root=${LOCAL_STORAGE_ROOT:-}
bind_host=${BIND_HOST:-0.0.0.0}
port=${PORT:-8080}
replace_config=false
without_power_manager=false
install_power_manager=false
enable_power_manager=false
deploy_capture_node=true

usage() {
  printf '%s\n' \
    "Usage: scripts/deploy.sh [options]" \
    "  --target USER@HOST" \
    "  --remote-base PATH" \
    "  --config FILE             v2 source config" \
    "  --replace-config          apply FILE even when an active config exists" \
    "  --capture-node-source PATH" \
    "  --capture-node-executable PATH" \
    "  --skip-capture-node-deploy  Use an externally managed CaptureNode" \
    "  --huintech-device-source PATH" \
    "  --power-manager-executable PATH" \
    "  --power-config PATH" \
    "  --power-state PATH" \
    "  --power-hold PATH" \
    "  --install-power-manager  Install/update root integration; preserve timer state" \
    "  --enable-power-manager   Install root integration and enable real shutdown timer" \
    "  --local-storage-root PATH" \
    "  --bind-host ADDRESS" \
    "  --port PORT"
}

while (($#)); do
  case "$1" in
    --target) target=${2:?missing value}; shift 2 ;;
    --remote-base) remote_base=${2:?missing value}; shift 2 ;;
    --config|--initial-config) config_file=${2:?missing value}; shift 2 ;;
    --replace-config|--update-settings) replace_config=true; shift ;;
    --capture-node-source) capture_source=${2:?missing value}; shift 2 ;;
    --capture-node-executable) capture_executable=${2:?missing value}; shift 2 ;;
    --skip-capture-node-deploy) deploy_capture_node=false; shift ;;
    --huintech-device-source) huintech_device_source=${2:?missing value}; shift 2 ;;
    --power-manager-executable) power_executable=${2:?missing value}; shift 2 ;;
    --power-config) power_config=${2:?missing value}; shift 2 ;;
    --power-state) power_state=${2:?missing value}; shift 2 ;;
    --power-hold) power_hold=${2:?missing value}; shift 2 ;;
    --install-power-manager) install_power_manager=true; shift ;;
    --enable-power-manager) install_power_manager=true; enable_power_manager=true; shift ;;
    --local-storage-root) local_storage_root=${2:?missing value}; shift 2 ;;
    --bind-host) bind_host=${2:?missing value}; shift 2 ;;
    --port) port=${2:?missing value}; shift 2 ;;
    --without-power-manager) without_power_manager=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ ]] || { printf 'Invalid target\n' >&2; exit 2; }
operator=${target%@*}
remote_base=${remote_base:-/home/$operator/pi-node-supervisor}
if [[ "$without_power_manager" == true ]]; then
  power_executable=''
  power_config=${power_config:-$remote_base/generated/pi-power-manager.json}
  power_state=${power_state:-$remote_base/state/pi-power-manager.json}
  power_hold=''
else
  power_config=${power_config:-/etc/pi-power-manager/config.json}
  power_state=${power_state:-/var/lib/pi-power-manager/state.json}
  power_hold=${power_hold:-/etc/pi-power-manager/supervisor.hold}
fi
local_storage_root=${local_storage_root:-$remote_base/storage}

[[ "$remote_base" == /* && "$remote_base" != / ]] || { printf 'Invalid remote base\n' >&2; exit 2; }
[[ -r "$config_file" ]] || { printf 'Config is unreadable: %s\n' "$config_file" >&2; exit 2; }
managed_capture_executable="/home/$operator/capture-node/.venv/bin/capture-node-web"
capture_executable=${capture_executable:-$managed_capture_executable}
if [[ "$deploy_capture_node" == true ]]; then
  [[ -x "$capture_source/eng/deploy.sh" ]] || {
    printf 'CaptureNode source is unavailable: %s\n' "$capture_source" >&2
    exit 2
  }
  [[ "$capture_executable" == "$managed_capture_executable" ]] || {
    printf '%s\n' \
      'Automatic CaptureNode deployment requires the standard target executable.' \
      'Use --skip-capture-node-deploy with a custom --capture-node-executable.' >&2
    exit 2
  }
fi
[[ "$port" =~ ^[0-9]+$ ]] && ((port > 0 && port < 65536)) || { printf 'Invalid port\n' >&2; exit 2; }
sensor_count=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1], encoding="utf-8")).get("sensors", {}).get("devices", {})))' "$config_file")
if ((sensor_count)); then
  [[ -r "$huintech_device_source/huintech/sensors/soil/cwt_soil_thc_s.py" ]] || {
    printf 'HUINTECHDEVICE sensor source is unavailable: %s\n' "$huintech_device_source" >&2
    exit 2
  }
fi

PYTHONPATH="$project_root/src" python3 -m pi_node_supervisor.cli validate-config "$config_file"
if [[ "$install_power_manager" == true ]]; then
  power_install_arguments=(
    --target "$target"
    --remote-base "$remote_base"
    --install-only
  )
  if [[ "$enable_power_manager" == true ]]; then
    power_install_arguments+=(--enable)
  else
    power_install_arguments+=(--preserve-enablement)
  fi
  "$script_dir/install-power-manager.sh" "${power_install_arguments[@]}"
fi
frontend="$project_root/src/pi_node_supervisor/web/frontend"
(cd "$frontend" && npm run build)

build_dir=$(mktemp -d /tmp/pi-node-supervisor-build.XXXXXX)
remote_stage=''
sensor_dependency_archive=''
cleanup() {
  code=$?
  [[ -z "$remote_stage" ]] || ssh -o ConnectTimeout=10 "$target" rm -rf -- "$remote_stage" >/dev/null 2>&1 || true
  rm -rf -- "$build_dir"
  exit "$code"
}
trap cleanup EXIT INT TERM

if python3 -c 'import build' >/dev/null 2>&1; then
  python3 -m build --wheel --outdir "$build_dir" "$project_root"
elif python3 -c 'import hatchling' >/dev/null 2>&1; then
  python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir "$build_dir" "$project_root"
else
  python3 -m venv "$build_dir/builder"
  "$build_dir/builder/bin/python" -m pip install build
  "$build_dir/builder/bin/python" -m build --wheel --outdir "$build_dir" "$project_root"
fi
wheel=$(find "$build_dir" -maxdepth 1 -name 'pi_node_supervisor-*.whl' -print -quit)
[[ -n "$wheel" ]] || { printf 'Wheel build failed\n' >&2; exit 1; }
release_id="$(date -u +%Y%m%dT%H%M%SZ)-$(sha256sum "$wheel" | cut -c1-12)"
if ((sensor_count)); then
  sensor_dependency_archive="$build_dir/huintech-device-python.tar.gz"
  tar --exclude='__pycache__' --exclude='*.pyc' \
    -C "$huintech_device_source" -czf "$sensor_dependency_archive" huintech
fi

if [[ "$deploy_capture_node" == true ]]; then
  printf '==> Ensuring CaptureNode runtime is current\n'
  "$capture_source/eng/deploy.sh" \
    --skip-tests \
    --skip-linger \
    --skip-if-current \
    --supervisor-managed \
    "$target"
fi

remote_stage=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" mktemp -d /tmp/pi-node-supervisor-deploy.XXXXXX)
scp -o ConnectTimeout=10 "$wheel" "$config_file" "$project_root/systemd/pi-node-supervisor.user.service.in" "$target:$remote_stage/"
if [[ -n "$sensor_dependency_archive" ]]; then
  scp -o ConnectTimeout=10 "$sensor_dependency_archive" "$target:$remote_stage/"
fi

ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" bash -s -- \
  "$remote_base" "$remote_stage" "$release_id" "$capture_executable" \
  "$power_executable" "$power_config" "$power_state" "$bind_host" "$port" \
  "$replace_config" "$local_storage_root" "$power_hold" <<'REMOTE'
set -Eeuo pipefail
base=$1; stage=$2; release_id=$3; capture=$4; power=$5; power_config=$6
power_state=$7; bind_host=$8; port=$9; replace_config=${10}
local_storage_root=${11}
power_hold=${12}
case "$base" in "$HOME"/*) ;; *) printf 'Install root must be below HOME\n' >&2; exit 2;; esac
wheel=$(find "$stage" -maxdepth 1 -name 'pi_node_supervisor-*.whl' -print -quit)
config=$(find "$stage" -maxdepth 1 -name '*.json' -print -quit)
template="$stage/pi-node-supervisor.user.service.in"
release="$base/releases/$release_id"
install -d -m 700 "$release" "$base/config" "$base/state" "$base/generated/capture-nodes" "$local_storage_root" "$HOME/.config/systemd/user"
python3 -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install "$wheel[web,sensor,remote]"
if [[ -r "$stage/huintech-device-python.tar.gz" ]]; then
  install -d -m 700 "$release/vendor"
  tar -xzf "$stage/huintech-device-python.tar.gz" -C "$release/vendor"
  PYTHONPATH="$release/vendor" "$release/.venv/bin/python" -c \
    'from huintech.sensors.soil.cwt_soil_thc_s import CWTSoilTHCS'
fi
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
power_hold_argument=''
if [[ -n "$power_hold" ]]; then
  power_hold_argument="--power-hold $power_hold"
fi
sed -e "s|@BIND_HOST@|$bind_host|g" -e "s|@PORT@|$port|g" \
  -e "s|@CAPTURE_NODE_EXECUTABLE@|$capture|g" \
  -e "s|@POWER_MANAGER_ARGUMENT@|$power_argument|g" \
  -e "s|@POWER_HOLD_ARGUMENT@|$power_hold_argument|g" \
  -e "s|@LOCAL_STORAGE_ROOT@|$local_storage_root|g" \
  -e "s|@POWER_CONFIG@|$power_config|g" -e "s|@POWER_STATE@|$power_state|g" \
  "$template" > "$HOME/.config/systemd/user/pi-node-supervisor.service"
systemctl --user daemon-reload
systemctl --user enable pi-node-supervisor.service >/dev/null
systemctl --user disable --now \
  capture-node-web@camera0.service capture-node-web@camera1.service >/dev/null 2>&1 || true
systemctl --user restart pi-node-supervisor.service
health=''
for _ in $(seq 1 120); do
  candidate=$(curl -fsS "http://127.0.0.1:$port/api/health" 2>/dev/null || true)
  if python3 -c 'import json,sys; raise SystemExit(json.loads(sys.argv[1]).get("schemaVersion") != 2)' "$candidate" 2>/dev/null; then
    health=$candidate
    break
  fi
  sleep 1
done
[[ -n "$health" ]] || { systemctl --user --no-pager --full status pi-node-supervisor.service >&2; exit 1; }
printf '%s\n' "$health"
REMOTE

printf '\nDeployment complete: http://%s:%s/\n' "${target#*@}" "$port"
