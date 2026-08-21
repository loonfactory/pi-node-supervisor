#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/.." && pwd)
power_source=${POWER_MANAGER_SOURCE:-$project_root/../pi-power-manager}
target=${TARGET:-huintech@192.168.12.7}
remote_base=${REMOTE_BASE:-}
nodes_file=${NODES_FILE:-}
initial_config=${INITIAL_CONFIG:-}
capture_node_executable=${CAPTURE_NODE_EXECUTABLE:-}
enable_timer=false
install_only=false

usage() {
  printf '%s\n' \
    "Usage: scripts/install-power-manager.sh [options]" \
    "" \
    "Installs the privileged RTC service. The timer stays disabled by default." \
    "" \
    "  --target USER@HOST       SSH destination (default: $target)" \
    "  --remote-base PATH       Supervisor root (default: /home/TARGET_USER/pi-node-supervisor)" \
    "  --power-source PATH      Local pi-power-manager source" \
    "  --nodes FILE             Node registry passed to Supervisor deployment" \
    "  --initial-config FILE    Initial Supervisor configuration" \
    "  --capture-node-executable PATH  Target CaptureNode Web executable" \
    "  --enable                 Enable automatic shutdown after diagnostics" \
    "  --preserve-enablement    Keep the target timer's current enabled state" \
    "  --install-only           Install only; do not redeploy Supervisor" \
    "  -h, --help               Show this help"
}

while (($#)); do
  case "$1" in
    --target) target=${2:?missing value for --target}; shift 2 ;;
    --remote-base) remote_base=${2:?missing value for --remote-base}; shift 2 ;;
    --power-source) power_source=${2:?missing value for --power-source}; shift 2 ;;
    --nodes) nodes_file=${2:?missing value for --nodes}; shift 2 ;;
    --initial-config) initial_config=${2:?missing value for --initial-config}; shift 2 ;;
    --capture-node-executable) capture_node_executable=${2:?missing value for --capture-node-executable}; shift 2 ;;
    --enable) enable_timer=true; shift ;;
    --preserve-enablement) enable_timer=preserve; shift ;;
    --install-only) install_only=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ ]] || { printf 'Invalid SSH target.\n' >&2; exit 2; }
if [[ ! -r "$power_source/pyproject.toml" ]]; then
  for candidate in "$project_root/../../pi-power-manager" "$project_root/../pi-power-manager"; do
    if [[ -r "$candidate/pyproject.toml" ]]; then
      power_source=$candidate
      break
    fi
  done
fi
[[ -r "$power_source/pyproject.toml" ]] || { printf 'pi-power-manager source is unavailable. Use --power-source PATH.\n' >&2; exit 2; }

operator=${target%@*}
remote_base=${remote_base:-/home/$operator/pi-node-supervisor}
[[ "$remote_base" == /* && "$remote_base" != / ]] || { printf 'Invalid remote base.\n' >&2; exit 2; }
capture_node_executable=${capture_node_executable:-/home/$operator/capture-node/.venv/bin/capture-node-web}
[[ -z "$nodes_file" || -r "$nodes_file" ]] || { printf 'Node registry is unreadable.\n' >&2; exit 2; }
[[ -z "$initial_config" || -r "$initial_config" ]] || { printf 'Initial configuration is unreadable.\n' >&2; exit 2; }
build_dir=$(mktemp -d /tmp/pi-power-manager-build.XXXXXX)
remote_stage=''
cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "$remote_stage" && "$remote_stage" == /tmp/pi-power-manager-deploy.* ]]; then
    ssh -o ConnectTimeout=10 "$target" rm -rf -- "$remote_stage" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$build_dir"
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

python_bin=${PYTHON_BIN:-python3}
if "$python_bin" -c 'import build' >/dev/null 2>&1; then
  "$python_bin" -m build --wheel --outdir "$build_dir" "$power_source"
elif "$python_bin" -c 'import hatchling' >/dev/null 2>&1; then
  "$python_bin" -m pip wheel --no-deps --no-build-isolation --wheel-dir "$build_dir" "$power_source"
else
  "$project_root/../capture-node/.venv/bin/python" -m pip wheel \
    --no-deps --no-build-isolation --wheel-dir "$build_dir" "$power_source"
fi
wheel=$(find "$build_dir" -maxdepth 1 -type f -name 'pi_power_manager-*.whl' -print -quit)
[[ -n "$wheel" ]] || { printf 'Wheel build failed.\n' >&2; exit 1; }
release_id="$(date -u +%Y%m%dT%H%M%SZ)-$(sha256sum "$wheel" | cut -c1-12)"

remote_stage=$(ssh -o ConnectTimeout=10 "$target" mktemp -d /tmp/pi-power-manager-deploy.XXXXXX)
scp -o ConnectTimeout=10 "$wheel" "$script_dir/install-power-manager-remote.sh" "$target:$remote_stage/"

printf 'Administrator authentication may be requested by %s.\n' "$target"
ssh -tt -o ConnectTimeout=10 "$target" \
  sudo bash "$remote_stage/install-power-manager-remote.sh" \
  "$operator" "$remote_base" "$remote_stage" "$release_id" "$enable_timer"

if [[ "$install_only" == true ]]; then
  printf 'RTC integration installed. timer_enabled=%s\n' "$enable_timer"
  exit 0
fi

# Point Supervisor at the protected configuration only after root preparation
# succeeds. This deployment remains unprivileged.
deploy_arguments=(
  --target "$target"
  --remote-base "$remote_base"
  --power-config /etc/pi-power-manager/config.json
  --capture-node-executable "$capture_node_executable"
  --without-power-manager
)
[[ -z "$nodes_file" ]] || deploy_arguments+=(--nodes "$nodes_file")
[[ -z "$initial_config" ]] || deploy_arguments+=(--initial-config "$initial_config")
"$script_dir/deploy.sh" "${deploy_arguments[@]}"

printf 'RTC integration complete. timer_enabled=%s\n' "$enable_timer"
