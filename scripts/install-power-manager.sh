#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/.." && pwd)
power_source=${POWER_MANAGER_SOURCE:-$project_root/../pi-power-manager}
target=${TARGET:-huintech@192.168.12.7}
remote_base=${REMOTE_BASE:-/home/huintech/pi-node-supervisor}
enable_timer=false

usage() {
  printf '%s\n' \
    "Usage: scripts/install-power-manager.sh [options]" \
    "" \
    "Installs the privileged RTC service. The timer stays disabled by default." \
    "" \
    "  --target USER@HOST       SSH destination (default: $target)" \
    "  --remote-base PATH       Supervisor root (default: $remote_base)" \
    "  --power-source PATH      Local pi-power-manager source" \
    "  --enable                 Enable automatic shutdown after diagnostics" \
    "  -h, --help               Show this help"
}

while (($#)); do
  case "$1" in
    --target) target=${2:?missing value for --target}; shift 2 ;;
    --remote-base) remote_base=${2:?missing value for --remote-base}; shift 2 ;;
    --power-source) power_source=${2:?missing value for --power-source}; shift 2 ;;
    --enable) enable_timer=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ ]] || { printf 'Invalid SSH target.\n' >&2; exit 2; }
[[ "$remote_base" == /* && "$remote_base" != / ]] || { printf 'Invalid remote base.\n' >&2; exit 2; }
[[ -r "$power_source/pyproject.toml" ]] || { printf 'pi-power-manager source is unavailable.\n' >&2; exit 2; }

operator=${target%@*}
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

# Point Supervisor at the protected configuration only after root preparation
# succeeds. This deployment remains unprivileged.
"$script_dir/deploy.sh" \
  --target "$target" \
  --remote-base "$remote_base" \
  --power-config /etc/pi-power-manager/config.json

printf 'RTC integration complete. timer_enabled=%s\n' "$enable_timer"
