#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/.." && pwd)

target=${TARGET:-huintech@192.168.12.7}
remote_base=${REMOTE_BASE:-/home/huintech/pi-node-supervisor}
bind_host=${BIND_HOST:-0.0.0.0}
port=${PORT:-8080}
nodes_file=${NODES_FILE:-$project_root/examples/nodes.dual-camera.json}
initial_config=${INITIAL_CONFIG:-$project_root/examples/config.empty.json}
python_bin=${PYTHON_BIN:-python3}
capture_node_executable=${CAPTURE_NODE_EXECUTABLE:-/home/huintech/capture-node/.venv/bin/capture-node-web}
replace_capture_services=${REPLACE_CAPTURE_SERVICES:-true}
preserve_existing_nodes=${PRESERVE_EXISTING_NODES:-false}
power_config=${POWER_CONFIG:-/etc/pi-power-manager/config.json}
huintech_device_source=${HUINTECHDEVICE_SOURCE:-}
power_manager_source=${POWER_MANAGER_SOURCE:-}
install_power_manager=true
enable_power=false

usage() {
  printf '%s\n' \
    "Usage: scripts/deploy.sh [options]" \
    "" \
    "Options:" \
    "  --target USER@HOST       SSH destination (default: $target)" \
    "  --remote-base PATH       Installation root (default: $remote_base)" \
    "  --bind-host ADDRESS      Web bind address (default: $bind_host)" \
    "  --port PORT              Web port (default: $port)" \
    "  --nodes FILE             Node registry JSON" \
    "  --initial-config FILE    Initial configuration JSON" \
    "  --capture-node-executable PATH  Trusted CaptureNode runtime" \
    "  --power-config PATH      pi-power-manager config path" \
    "  --power-manager-source PATH  Local pi-power-manager source root" \
    "  --enable-power           Enable automatic shutdown after diagnostics" \
    "  --without-power-manager  Skip privileged manager installation" \
    "  --huintech-device-source PATH  HUINTECHDEVICE python source root" \
    "  --preserve-existing-nodes  Keep and validate the target's existing nodes.json" \
    "  --keep-external-capture-nodes   Do not disable legacy user services" \
    "  -h, --help               Show this help"
}

while (($#)); do
  case "$1" in
    --target) target=${2:?missing value for --target}; shift 2 ;;
    --remote-base) remote_base=${2:?missing value for --remote-base}; shift 2 ;;
    --bind-host) bind_host=${2:?missing value for --bind-host}; shift 2 ;;
    --port) port=${2:?missing value for --port}; shift 2 ;;
    --nodes) nodes_file=${2:?missing value for --nodes}; shift 2 ;;
    --initial-config) initial_config=${2:?missing value for --initial-config}; shift 2 ;;
    --capture-node-executable) capture_node_executable=${2:?missing value for --capture-node-executable}; shift 2 ;;
    --power-config) power_config=${2:?missing value for --power-config}; shift 2 ;;
    --power-manager-source) power_manager_source=${2:?missing value for --power-manager-source}; shift 2 ;;
    --enable-power) enable_power=true; shift ;;
    --without-power-manager) install_power_manager=false; shift ;;
    --huintech-device-source) huintech_device_source=${2:?missing value for --huintech-device-source}; shift 2 ;;
    --preserve-existing-nodes) preserve_existing_nodes=true; shift ;;
    --keep-external-capture-nodes) replace_capture_services=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ ]] || {
  printf 'Invalid SSH target: %s\n' "$target" >&2
  exit 2
}
[[ "$remote_base" == /* && "$remote_base" != / && "$remote_base" != */..* ]] || {
  printf 'REMOTE_BASE must be a specific absolute path\n' >&2
  exit 2
}
[[ "$capture_node_executable" =~ ^/[A-Za-z0-9._/-]+$ ]] || {
  printf 'CaptureNode executable must be an absolute path\n' >&2
  exit 2
}
[[ "$power_config" =~ ^/[A-Za-z0-9._/-]+$ ]] || {
  printf 'Power manager configuration must be an absolute path\n' >&2
  exit 2
}
[[ "$power_config" == "$remote_base/generated/pi-power-manager.json" || \
   "$power_config" == /etc/pi-power-manager/config.json ]] || {
  printf 'Power manager configuration must use the staged or privileged supported path\n' >&2
  exit 2
}
[[ "$bind_host" =~ ^[0-9A-Fa-f:.]+$ ]] || {
  printf 'BIND_HOST must be an IP address\n' >&2
  exit 2
}
[[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || {
  printf 'PORT must be between 1 and 65535\n' >&2
  exit 2
}
[[ -r "$nodes_file" && -r "$initial_config" ]] || {
  printf 'Node registry or initial configuration is unreadable\n' >&2
  exit 2
}
if [[ "$install_power_manager" == true ]]; then
  if [[ -z "$power_manager_source" ]]; then
    for candidate in "$project_root/../../pi-power-manager" "$project_root/../pi-power-manager"; do
      if [[ -r "$candidate/pyproject.toml" ]]; then
        power_manager_source=$candidate
        break
      fi
    done
  fi
  [[ -n "$power_manager_source" && -r "$power_manager_source/pyproject.toml" ]] || {
    printf 'deploy.sh includes pi-power-manager; use --power-manager-source PATH when its source cannot be found\n' >&2
    exit 2
  }
  [[ "$power_config" == /etc/pi-power-manager/config.json ]] || {
    printf 'The integrated deployment requires --power-config /etc/pi-power-manager/config.json\n' >&2
    exit 2
  }
fi
sensor_count=$(
  "$python_bin" -c 'import json,sys; print(sum(node.get("sensor_serial") is not None for node in json.load(open(sys.argv[1], encoding="utf-8"))["nodes"]))' "$nodes_file"
)
if ((sensor_count)); then
  command -v tar >/dev/null || { printf 'Local tar command is required for HUINTECHDEVICE staging\n' >&2; exit 2; }
  if [[ -z "$huintech_device_source" ]]; then
    for candidate in \
      "$project_root/../huintech-device/huintech-device-main/python" \
      "$project_root/../../../huintech-device/huintech-device-main/python"; do
      if [[ -r "$candidate/huintech/sensors/soil/cwt_soil_thc_s.py" ]]; then
        huintech_device_source=$candidate
        break
      fi
    done
  fi
  [[ -n "$huintech_device_source" && -r "$huintech_device_source/huintech/sensors/soil/cwt_soil_thc_s.py" ]] || {
    printf 'Sensor nodes require --huintech-device-source pointing to the python source root\n' >&2
    exit 2
  }
fi

ssh_options=(-o BatchMode=yes -o ConnectTimeout=10)
build_dir=$(mktemp -d /tmp/pi-node-supervisor-build.XXXXXX)
remote_stage=''
sensor_dependency_archive=''

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "$remote_stage" && "$remote_stage" == /tmp/pi-node-supervisor-deploy.* ]]; then
    ssh "${ssh_options[@]}" "$target" rm -rf -- "$remote_stage" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$build_dir"
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

printf 'Preflight: %s\n' "$target"
ssh "${ssh_options[@]}" "$target" 'set -eu; command -v python3 >/dev/null; command -v systemctl >/dev/null; command -v curl >/dev/null; command -v tar >/dev/null; systemctl --user show-environment >/dev/null; printf "host=%s arch=%s python=%s\n" "$(hostname)" "$(uname -m)" "$(python3 -V 2>&1)"'

deployment_power_config=$power_config
if [[ "$install_power_manager" == true ]] && \
   ! ssh "${ssh_options[@]}" "$target" test -r /etc/pi-power-manager/config.json; then
  deployment_power_config="$remote_base/generated/pi-power-manager.json"
fi

printf 'Building wheel\n'
if "$python_bin" -c 'import build' >/dev/null 2>&1; then
  "$python_bin" -m build --wheel --outdir "$build_dir" "$project_root"
elif "$python_bin" -c 'import hatchling' >/dev/null 2>&1; then
  "$python_bin" -m pip wheel --no-deps --no-build-isolation --wheel-dir "$build_dir" "$project_root"
elif [[ -x "$project_root/../capture-node/.venv/bin/python" ]] && \
     "$project_root/../capture-node/.venv/bin/python" -c 'import hatchling' >/dev/null 2>&1; then
  "$project_root/../capture-node/.venv/bin/python" -m pip wheel \
    --no-deps --no-build-isolation --wheel-dir "$build_dir" "$project_root"
else
  "$python_bin" -m venv "$build_dir/builder"
  "$build_dir/builder/bin/python" -m pip install build
  "$build_dir/builder/bin/python" -m build --wheel --outdir "$build_dir" "$project_root"
fi
wheel_path=$(find "$build_dir" -maxdepth 1 -type f -name 'pi_node_supervisor-*.whl' -print -quit)
[[ -n "$wheel_path" ]] || {
  printf 'Wheel build did not produce an artifact\n' >&2
  exit 1
}
wheel_hash=$(sha256sum "$wheel_path" | awk '{print $1}')
release_id="$(date -u +%Y%m%dT%H%M%SZ)-${wheel_hash:0:12}"
if ((sensor_count)); then
  sensor_dependency_archive="$build_dir/huintech-device-python.tar.gz"
  tar --exclude='__pycache__' --exclude='*.pyc' \
    -C "$huintech_device_source" -czf "$sensor_dependency_archive" huintech
fi

remote_stage=$(ssh "${ssh_options[@]}" "$target" mktemp -d /tmp/pi-node-supervisor-deploy.XXXXXX)
[[ "$remote_stage" == /tmp/pi-node-supervisor-deploy.* ]] || {
  printf 'Unexpected remote staging path\n' >&2
  exit 1
}

printf 'Uploading release %s\n' "$release_id"
scp "${ssh_options[@]}" \
  "$wheel_path" \
  "$nodes_file" \
  "$initial_config" \
  "$project_root/systemd/pi-node-supervisor.user.service.in" \
  "$target:$remote_stage/"
if [[ -n "$sensor_dependency_archive" ]]; then
  scp "${ssh_options[@]}" "$sensor_dependency_archive" "$target:$remote_stage/"
fi

printf 'Installing and activating release\n'
ssh "${ssh_options[@]}" "$target" bash -s -- \
  "$remote_base" "$remote_stage" "$release_id" "$bind_host" "$port" \
  "$capture_node_executable" "$replace_capture_services" "$deployment_power_config" \
  "$preserve_existing_nodes" <<'REMOTE_INSTALL'
set -Eeuo pipefail

base=$1
stage=$2
release_id=$3
bind_host=$4
port=$5
capture_node_executable=$6
replace_capture_services=$7
power_config=$8
preserve_existing_nodes=$9

case "$base" in
  "$HOME"/*) ;;
  *) printf 'Remote base must be below the SSH user home\n' >&2; exit 2 ;;
esac
case "$stage" in
  /tmp/pi-node-supervisor-deploy.*) ;;
  *) printf 'Invalid staging path\n' >&2; exit 2 ;;
esac

wheel=$(find "$stage" -maxdepth 1 -type f -name 'pi_node_supervisor-*.whl' -print -quit)
nodes=$(find "$stage" -maxdepth 1 -type f -name 'nodes*.json' -print -quit)
initial=$(find "$stage" -maxdepth 1 -type f -name 'config*.json' -print -quit)
unit_template="$stage/pi-node-supervisor.user.service.in"
[[ -r "$wheel" && -r "$nodes" && -r "$initial" && -r "$unit_template" ]]
effective_nodes=$nodes
if [[ "$preserve_existing_nodes" == true && -f "$base/config/nodes.json" ]]; then
  effective_nodes="$base/config/nodes.json"
  printf 'Preserving existing node registry: %s\n' "$effective_nodes"
fi
if [[ "$replace_capture_services" == true ]]; then
  [[ -x "$capture_node_executable" ]] || {
    printf 'CaptureNode executable is unavailable: %s\n' "$capture_node_executable" >&2
    exit 1
  }
fi

release="$base/releases/$release_id"
config_dir="$base/config"
state_dir="$base/state"
generated_dir="$base/generated"
backup_dir="$base/backups"
unit_dir="$HOME/.config/systemd/user"
unit_path="$unit_dir/pi-node-supervisor.service"
stamp=$(date -u +%Y%m%dT%H%M%SZ)

install -d -m 700 "$base" "$base/releases" "$release" "$config_dir" "$state_dir" "$generated_dir" "$backup_dir" "$unit_dir"
python3 -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install "$wheel[web,sensor,remote]"
if [[ -r "$stage/huintech-device-python.tar.gz" ]]; then
  install -d -m 700 "$release/vendor"
  tar -xzf "$stage/huintech-device-python.tar.gz" -C "$release/vendor"
  PYTHONPATH="$release/vendor" "$release/.venv/bin/python" -c \
    'from huintech.sensors.soil.cwt_soil_thc_s import CWTSoilTHCS'
fi

"$release/.venv/bin/pi-node-supervisor" \
  --nodes "$effective_nodes" \
  --state-dir "$state_dir" \
  --active-config "$config_dir/config.json" \
  --power-config "$power_config" \
  validate-config "$initial"

python3 - "$effective_nodes" <<'PYTHON_PREFLIGHT'
import json
import sys
import urllib.request

for node in json.load(open(sys.argv[1], encoding="utf-8"))["nodes"]:
    endpoint = node.get("endpoint")
    if not endpoint:
        continue
    with urllib.request.urlopen(endpoint + "/api/health", timeout=10) as response:
        payload = json.load(response)
    if payload.get("status") != "running":
        raise SystemExit(f"{node['id']} is not running")
    print(f"node={node['id']} status=running")
PYTHON_PREFLIGHT

PYTHONPATH="$release/vendor${PYTHONPATH:+:$PYTHONPATH}" \
  "$release/.venv/bin/python" - "$effective_nodes" "$base" <<'PYTHON_SENSOR_PREFLIGHT'
import json
import os
import sys
from pathlib import Path

sensor_nodes = [
    node for node in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["nodes"]
    if node.get("sensor_serial") is not None
]
base = Path(sys.argv[2]).resolve()
if sensor_nodes:
    from huintech.sensors.soil.cwt_soil_thc_s import CWTSoilTHCS  # noqa: F401
    import serial  # noqa: F401
for node in sensor_nodes:
    port = Path(node["sensor_serial"]["port"])
    if not port.exists():
        raise SystemExit(f"sensor port is unavailable: {port}")
    if not os.access(port, os.R_OK | os.W_OK):
        raise SystemExit(
            f"sensor port requires read/write permission for {os.environ.get('USER', 'service user')}: {port}"
        )
    configured_root = Path(node["sensor_storage"]["local_directory"])
    storage_root = (
        configured_root if configured_root.is_absolute() else base / configured_root
    ).resolve()
    try:
        storage_root.relative_to(base)
        below_base = True
    except ValueError:
        below_base = False
    if not storage_root.exists() and below_base:
        storage_root.mkdir(parents=True, mode=0o700)
    if not storage_root.is_dir() or not os.access(storage_root, os.R_OK | os.W_OK | os.X_OK):
        raise SystemExit(f"sensor CSV directory requires service-user access: {storage_root}")
    print(f"node={node['id']} sensor_port={port} permission=read-write")
PYTHON_SENSOR_PREFLIGHT

nodes_backup=''
if [[ "$effective_nodes" != "$config_dir/nodes.json" ]]; then
  if [[ -f "$config_dir/nodes.json" ]] && ! cmp -s "$config_dir/nodes.json" "$nodes"; then
    nodes_backup="$backup_dir/nodes.json.$stamp"
    cp -p "$config_dir/nodes.json" "$nodes_backup"
  fi
  install -m 600 "$nodes" "$config_dir/nodes.json"
fi
install -m 600 "$initial" "$config_dir/initial-config.json"

python3 - "$config_dir/nodes.json" "$HOME/capture-node" <<'PYTHON_MANAGED_CONFIGS'
import json
import os
import sys
from pathlib import Path

registry = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
source_root = Path(sys.argv[2])
for node in registry["nodes"]:
    managed = node.get("managed_capture")
    if not managed:
        continue
    destination = Path(managed["config_path"])
    if destination.exists():
        continue
    source = source_root / f"config.{node['id']}.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
    os.replace(temporary, destination)
PYTHON_MANAGED_CONFIGS

if ! grep -Eq '^PI_NODE_SUPERVISOR_API_TOKEN=.{20,}$' "$config_dir/supervisor.env" 2>/dev/null; then
  umask 077
  "$release/.venv/bin/python" -c 'import secrets; print("PI_NODE_SUPERVISOR_API_TOKEN=" + secrets.token_urlsafe(32))' > "$config_dir/supervisor.env"
fi
chmod 600 "$config_dir/supervisor.env"

first_install=false
if [[ ! -f "$config_dir/config.json" ]]; then
  first_install=true
  bootstrap="$config_dir/bootstrap-config.json"
  python3 - "$config_dir/initial-config.json" "$config_dir/nodes.json" "$bootstrap" <<'PYTHON_BOOTSTRAP'
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

initial_path, registry_path, output_path = map(Path, sys.argv[1:])
document = json.loads(initial_path.read_text(encoding="utf-8"))
registry = json.loads(registry_path.read_text(encoding="utf-8"))
default_capture_times = [
    f"{minutes // 60:02d}:{minutes % 60:02d}:00"
    for minutes in range(5 * 60, 20 * 60 + 1, 30)
]
default_sensor_times = [
    f"{minutes // 60:02d}:{minutes % 60:02d}:00"
    for minutes in range(0, 24 * 60, 30)
]
configured_capture_schedules = {
    schedule["node"]: schedule
    for schedule in document.get("capture_schedules", [])
}
schedules = []
for node in registry["nodes"]:
    managed = node.get("managed_capture")
    if not managed:
        continue
    capture = json.loads(
        Path(managed["config_path"]).read_text(encoding="utf-8")
    ).get("capture", {})
    schedule = configured_capture_schedules.get(node["id"])
    if schedule is None and capture.get("mode") == "disabled":
        schedule = {
            "id": f"auto-{node['id']}",
            "node": node["id"],
            "profile": (node.get("profiles") or ["default"])[0],
            "mode": "daily",
            "interval_seconds": None,
            "daily_times": default_capture_times,
            "timezone": document.get("timezone", "Asia/Seoul"),
            "parameters": {},
        }
    elif schedule is None:
        schedule = {
            "id": f"auto-{node['id']}",
            "node": node["id"],
            "profile": (node.get("profiles") or ["default"])[0],
            "mode": capture.get("mode"),
            "interval_seconds": capture.get("interval_seconds"),
            "daily_times": capture.get("daily_times", []),
            "timezone": capture.get("timezone", document.get("timezone", "Asia/Seoul")),
            "parameters": {},
        }
    if schedule["mode"] == "interval":
        interval = float(schedule["interval_seconds"])
        schedule.setdefault("anchor_at", (
            datetime.now(UTC) + timedelta(seconds=interval)
        ).isoformat().replace("+00:00", "Z"))
    schedules.append(schedule)
document["capture_schedules"] = schedules
configured_sensor_nodes = {
    schedule["node"]
    for schedule in document.get("sensor_schedules", [])
}
sensor_schedules = list(document.get("sensor_schedules", []))
for node in registry["nodes"]:
    if node.get("sensor_serial") is None or node["id"] in configured_sensor_nodes:
        continue
    sensor_schedules.append({
        "id": f"auto-sensor-{node['id']}",
        "node": node["id"],
        "profile": (node.get("profiles") or ["temperature-humidity"])[0],
        "mode": "daily",
        "daily_times": default_sensor_times,
        "timezone": document.get("timezone", "Asia/Seoul"),
        "parameters": {},
    })
document["sensor_schedules"] = sensor_schedules
document.setdefault("power_keep_schedules", [
    {
        "id": "default-morning-power",
        "start": "08:50:00",
        "end": "09:30:00",
        "timezone": document.get("timezone", "Asia/Seoul"),
    },
    {
        "id": "default-evening-power",
        "start": "17:20:00",
        "end": "18:10:00",
        "timezone": document.get("timezone", "Asia/Seoul"),
    },
])
output_path.write_text(
    json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
)
PYTHON_BOOTSTRAP
  "$release/.venv/bin/pi-node-supervisor" \
    --nodes "$config_dir/nodes.json" \
    --state-dir "$state_dir" \
    --active-config "$config_dir/config.json" \
    --power-config "$power_config" \
    apply "$bootstrap"
fi

old_current=$(readlink "$base/current" 2>/dev/null || true)
if [[ -n "$old_current" ]]; then
  ln -sfn "$old_current" "$base/previous"
fi
ln -sfn "$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"

unit_backup=''
if [[ -f "$unit_path" ]]; then
  unit_backup="$backup_dir/pi-node-supervisor.service.$stamp"
  cp -p "$unit_path" "$unit_backup"
fi
sed \
  -e "s|@BIND_HOST@|$bind_host|g" \
  -e "s|@PORT@|$port|g" \
  -e "s|@CAPTURE_NODE_EXECUTABLE@|$capture_node_executable|g" \
  -e "s|@POWER_CONFIG@|$power_config|g" \
  "$unit_template" > "$release/pi-node-supervisor.service"
install -m 644 "$release/pi-node-supervisor.service" "$unit_path"

activation_ok=false
legacy_units=(capture-node-web@camera0.service capture-node-web@camera1.service)
legacy_active=()
legacy_enabled=()
if [[ "$replace_capture_services" == true ]]; then
  for unit in "${legacy_units[@]}"; do
    if systemctl --user is-active --quiet "$unit"; then
      legacy_active+=("$unit")
    fi
    if systemctl --user is-enabled --quiet "$unit"; then
      legacy_enabled+=("$unit")
    fi
  done
  systemctl --user stop "${legacy_units[@]}"
  systemctl --user disable "${legacy_units[@]}" >/dev/null
fi
if systemctl --user daemon-reload && \
   systemctl --user enable pi-node-supervisor.service >/dev/null && \
   systemctl --user restart pi-node-supervisor.service; then
  # A forced stop can leave camera devices settling long enough for the first
  # managed CaptureNode startup to time out and recover once via systemd.
  for _attempt in $(seq 1 180); do
    if curl --fail --silent "http://127.0.0.1:$port/api/health" >/dev/null; then
      activation_ok=true
      break
    fi
    sleep 1
  done
fi

if [[ "$activation_ok" != true ]]; then
  printf 'Activation failed; restoring the previous unit and release\n' >&2
  systemctl --user --no-pager --full status pi-node-supervisor.service >&2 || true
  if [[ -n "$old_current" ]]; then
    ln -sfn "$old_current" "$base/current.next"
    mv -Tf "$base/current.next" "$base/current"
  fi
  if [[ -n "$unit_backup" ]]; then
    install -m 644 "$unit_backup" "$unit_path"
  fi
  if [[ -n "$nodes_backup" ]]; then
    install -m 600 "$nodes_backup" "$config_dir/nodes.json"
  fi
  systemctl --user daemon-reload || true
  if [[ "$replace_capture_services" == true ]]; then
    if ((${#legacy_enabled[@]})); then
      systemctl --user enable "${legacy_enabled[@]}" >/dev/null || true
    fi
    if ((${#legacy_active[@]})); then
      systemctl --user start "${legacy_active[@]}" || true
    fi
  fi
  systemctl --user restart pi-node-supervisor.service || true
  exit 1
fi

api_token=${PI_NODE_SUPERVISOR_API_TOKEN:-}
if [[ -z "$api_token" ]]; then
  api_token=$(sed -n 's/^PI_NODE_SUPERVISOR_API_TOKEN=//p' "$config_dir/supervisor.env")
fi
curl --fail --silent --show-error \
  -H "X-Supervisor-Token: $api_token" \
  "http://127.0.0.1:$port/api/dashboard" >/dev/null
unset api_token

systemctl --user is-active pi-node-supervisor.service
printf 'release=%s first_install=%s endpoint=http://%s:%s/\n' "$release_id" "$first_install" "$(hostname -I | awk '{print $1}')" "$port"
REMOTE_INSTALL

if [[ "$install_power_manager" == true ]]; then
  printf 'Installing pi-power-manager privileged service\n'
  power_arguments=(
    --target "$target"
    --remote-base "$remote_base"
    --power-source "$power_manager_source"
    --install-only
  )
  if [[ "$enable_power" == true ]]; then
    power_arguments+=(--enable)
  else
    power_arguments+=(--preserve-enablement)
  fi
  "$script_dir/install-power-manager.sh" "${power_arguments[@]}"

  if [[ "$deployment_power_config" != "$power_config" ]]; then
    printf 'Connecting Supervisor to %s\n' "$power_config"
    ssh "${ssh_options[@]}" "$target" bash -s -- \
      "$remote_base" "$deployment_power_config" "$power_config" "$port" <<'REMOTE_POWER_SWITCH'
set -Eeuo pipefail
base=$1
old_path=$2
new_path=$3
port=$4
release_unit="$base/current/pi-node-supervisor.service"
user_unit="$HOME/.config/systemd/user/pi-node-supervisor.service"
backup=$(mktemp /tmp/pi-node-supervisor-unit.XXXXXX)
release_backup=$(mktemp /tmp/pi-node-supervisor-release-unit.XXXXXX)
cp -p "$user_unit" "$backup"
cp -p "$release_unit" "$release_backup"
cleanup() { rm -f -- "$backup" "$release_backup"; }
trap cleanup EXIT
sed "s|--power-config $old_path|--power-config $new_path|" "$release_unit" > "$user_unit.next"
grep -Fq -- "--power-config $new_path" "$user_unit.next"
mv -f "$user_unit.next" "$release_unit"
install -m 644 "$release_unit" "$user_unit"
if ! systemctl --user daemon-reload || ! systemctl --user restart pi-node-supervisor.service; then
  install -m 644 "$release_backup" "$release_unit"
  install -m 644 "$backup" "$user_unit"
  systemctl --user daemon-reload || true
  systemctl --user restart pi-node-supervisor.service || true
  exit 1
fi
for _attempt in $(seq 1 180); do
  if curl --fail --silent "http://127.0.0.1:$port/api/health" >/dev/null; then
    exit 0
  fi
  sleep 1
done
install -m 644 "$release_backup" "$release_unit"
install -m 644 "$backup" "$user_unit"
systemctl --user daemon-reload || true
systemctl --user restart pi-node-supervisor.service || true
printf 'Supervisor failed after switching to the privileged power configuration\n' >&2
exit 1
REMOTE_POWER_SWITCH
  fi
fi

printf 'Deployment complete: http://%s:%s/\n' "${target#*@}" "$port"
printf 'The API token remains on the device at %s/config/supervisor.env\n' "$remote_base"
