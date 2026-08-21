#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_APP_DIR="capture-node"
SUPPORTED_INSTANCE_NAMES=("camera0" "camera1")
SUPPORTED_INSTANCE_PORTS=(8000 8001)
CAMERA_COUNT=""
CAMERA_COUNT_EXPLICIT=0
CAMERA_SPECS=()
SKIP_TESTS=0
SKIP_LINGER=0
DRY_RUN=0
BOOTSTRAP_SSH=0
SSH_PUBLIC_KEY="${HOME}/.ssh/id_ed25519.pub"
OFFLINE=0
CACHE_WHEELS=0
WHEELHOUSE=""
TARGET=""

usage() {
    cat <<'EOF'
Usage: eng/deploy.sh [options] <user@host>

Build and deploy CaptureNode Web camera instances to ~/capture-node.
One Picamera2 instance on port 8000 is deployed by default. Repeat --camera to
describe one or two cameras. Existing configurations, captures/, and .venv/ are
preserved. Camera options apply only when an instance configuration is missing.

Options:
  --camera SPEC       Initial camera: picamera2[:N] or v4l2:/dev/path.
                      Repeat to deploy two instances.
  --camera-count N    Compatibility option: deploy N Picamera2 instances.
  --bootstrap-ssh     Accept a new host key and install the local public key.
  --public-key FILE   Public key for --bootstrap-ssh
                      (default: ~/.ssh/id_ed25519.pub).
  --offline           Never use a remote package index. Reuse the installed
                      runtime or install only from a remote/uploaded wheelhouse.
  --wheelhouse DIR    Upload a compatible Python wheelhouse before activation.
  --cache-wheels      Cache runtime wheels remotely during an online deployment.
  --skip-tests        Skip the local pytest run.
  --skip-linger       Skip systemd linger setup when provisioned separately.
  --dry-run           Build locally and print the deployment plan only.
  -h, --help          Show this help.

Example:
  eng/deploy.sh huintech@capture-node.local
  eng/deploy.sh --bootstrap-ssh --camera v4l2:/dev/video0 user@host
  eng/deploy.sh --camera picamera2:0 --camera picamera2:1 user@host
  eng/deploy.sh --offline --camera-count 2 user@host
EOF
}

fail() {
    printf 'deploy: %s\n' "$*" >&2
    exit 1
}

while (($#)); do
    case "$1" in
        --camera-count)
            (($# >= 2)) || fail "--camera-count requires a value"
            CAMERA_COUNT="$2"
            CAMERA_COUNT_EXPLICIT=1
            shift
            ;;
        --camera-count=*)
            CAMERA_COUNT="${1#*=}"
            CAMERA_COUNT_EXPLICIT=1
            ;;
        --camera)
            (($# >= 2)) || fail "--camera requires a value"
            CAMERA_SPECS+=("$2")
            shift
            ;;
        --camera=*)
            CAMERA_SPECS+=("${1#*=}")
            ;;
        --bootstrap-ssh)
            BOOTSTRAP_SSH=1
            ;;
        --public-key)
            (($# >= 2)) || fail "--public-key requires a value"
            SSH_PUBLIC_KEY="$2"
            shift
            ;;
        --public-key=*)
            SSH_PUBLIC_KEY="${1#*=}"
            ;;
        --offline)
            OFFLINE=1
            ;;
        --wheelhouse)
            (($# >= 2)) || fail "--wheelhouse requires a value"
            WHEELHOUSE="$2"
            shift
            ;;
        --wheelhouse=*)
            WHEELHOUSE="${1#*=}"
            ;;
        --cache-wheels)
            CACHE_WHEELS=1
            ;;
        --skip-tests)
            SKIP_TESTS=1
            ;;
        --skip-linger)
            SKIP_LINGER=1
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            fail "unknown option: $1"
            ;;
        *)
            [[ -z "$TARGET" ]] || fail "only one deployment target is allowed"
            TARGET="$1"
            ;;
    esac
    shift
done

if ((${#CAMERA_SPECS[@]} > 0)); then
    ((CAMERA_COUNT_EXPLICIT == 0)) \
        || fail "--camera and --camera-count cannot be used together"
    CAMERA_COUNT="${#CAMERA_SPECS[@]}"
else
    CAMERA_COUNT="${CAMERA_COUNT:-1}"
    [[ "$CAMERA_COUNT" =~ ^[12]$ ]] || fail "one or two cameras are supported"
    for ((index = 0; index < CAMERA_COUNT; index++)); do
        CAMERA_SPECS+=("picamera2:${index}")
    done
fi
[[ "$CAMERA_COUNT" =~ ^[12]$ ]] || fail "one or two cameras are supported"
for camera_spec in "${CAMERA_SPECS[@]}"; do
    if [[ "$camera_spec" == "picamera2" \
        || "$camera_spec" =~ ^picamera2:[0-9]+$ \
        || "$camera_spec" =~ ^v4l2:/.+$ ]]; then
        continue
    fi
    fail "invalid --camera value: $camera_spec"
done
INSTANCE_NAMES=("${SUPPORTED_INSTANCE_NAMES[@]:0:CAMERA_COUNT}")
INSTANCE_PORTS=("${SUPPORTED_INSTANCE_PORTS[@]:0:CAMERA_COUNT}")

[[ -n "$TARGET" ]] || {
    usage >&2
    exit 2
}
[[ "$TARGET" != *:* ]] || fail "target must be user@host without a remote path"
for command in npm rsync ssh; do
    command -v "$command" >/dev/null || fail "required command is missing: $command"
done
if ((BOOTSTRAP_SSH == 1)); then
    command -v ssh-copy-id >/dev/null \
        || fail "--bootstrap-ssh requires ssh-copy-id"
    [[ -f "$SSH_PUBLIC_KEY" ]] \
        || fail "public key does not exist: $SSH_PUBLIC_KEY"
fi
if [[ -n "$WHEELHOUSE" ]]; then
    [[ -d "$WHEELHOUSE" ]] || fail "wheelhouse does not exist: $WHEELHOUSE"
    compgen -G "$WHEELHOUSE/*.whl" >/dev/null \
        || fail "wheelhouse contains no .whl files: $WHEELHOUSE"
    WHEELHOUSE="$(cd -- "$WHEELHOUSE" && pwd)"
fi
((OFFLINE == 0 || CACHE_WHEELS == 0)) \
    || fail "--cache-wheels requires an online deployment"

cd "$ROOT_DIR"

if ((SKIP_TESTS == 0)); then
    [[ -x .venv/bin/pytest ]] || fail "local .venv/bin/pytest is missing"
    printf '==> Running tests\n'
    .venv/bin/pytest -q
fi

if [[ ! -d src/capture_node/web/frontend/node_modules ]]; then
    printf '==> Installing frontend dependencies\n'
    npm --prefix src/capture_node/web/frontend ci
fi

printf '==> Building Web frontend\n'
npm --prefix src/capture_node/web/frontend run build

if ((DRY_RUN == 1)); then
    printf '==> Dry run complete\n'
    printf 'Target: %s:~/%s\n' "$TARGET" "$REMOTE_APP_DIR"
    for index in "${!INSTANCE_NAMES[@]}"; do
        printf 'Instance: %s | port %s | initial camera %s\n' \
            "${INSTANCE_NAMES[$index]}" \
            "${INSTANCE_PORTS[$index]}" \
            "${CAMERA_SPECS[$index]}"
    done
    if ((OFFLINE == 1)); then
        printf 'Packages: offline (installed runtime or wheelhouse only)\n'
    elif ((CACHE_WHEELS == 1)); then
        printf 'Packages: online and cache remote wheelhouse\n'
    else
        printf 'Packages: online fallback allowed\n'
    fi
    exit 0
fi

SSH_OPTIONS=(
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o StrictHostKeyChecking=accept-new
)
RSYNC_SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"

printf '==> Checking SSH access\n'
if ! ssh "${SSH_OPTIONS[@]}" "$TARGET" true; then
    if ((BOOTSTRAP_SSH == 0)); then
        fail "SSH key authentication failed; rerun with --bootstrap-ssh for first setup"
    fi
    [[ -t 0 ]] \
        || fail "--bootstrap-ssh requires an interactive terminal for the remote password"
    printf '==> Installing SSH public key (the remote password may be requested)\n'
    ssh-copy-id \
        -o StrictHostKeyChecking=accept-new \
        -i "$SSH_PUBLIC_KEY" \
        "$TARGET"
    ssh "${SSH_OPTIONS[@]}" "$TARGET" true \
        || fail "SSH key authentication still fails after bootstrap"
fi

printf '==> Checking remote deployment\n'
ssh "${SSH_OPTIONS[@]}" "$TARGET" \
    bash -s -- "$REMOTE_APP_DIR" <<'REMOTE_CHECK' \
    || fail "remote deployment prerequisites are unavailable"
set -u

missing=0

for command_name in curl journalctl loginctl python3 rsync systemctl; do
    if ! command -v "$command_name" >/dev/null; then
        printf 'Missing remote command: %s\n' "$command_name" >&2
        missing=1
    fi
done

exit "$missing"
REMOTE_CHECK

printf '==> Preparing remote installation\n'
ssh "${SSH_OPTIONS[@]}" "$TARGET" \
    bash -s -- "$REMOTE_APP_DIR" "$CAMERA_COUNT" <<'REMOTE_PREPARE'
set -eu

app_dir="$1"
camera_count="$2"
mkdir -p \
    "$app_dir/captures" \
    "$app_dir/instances" \
    "$app_dir/.wheelhouse" \
    "$app_dir/.deploy-next/capture_node"
for ((index = 0; index < camera_count; index++)); do
    mkdir -p "$app_dir/captures/camera${index}"
done
REMOTE_PREPARE

if ((SKIP_LINGER == 0)); then
    printf '==> Enabling login-independent user service startup\n'
    ssh -tt "${SSH_OPTIONS[@]}" "$TARGET" '
        remote_user="$(id -un)"
        linger="$(loginctl show-user "$remote_user" --property=Linger --value)"
        if [ "$linger" != "yes" ]; then
            if [ "$(id -u)" -eq 0 ]; then
                loginctl enable-linger "$remote_user"
            else
                command -v sudo >/dev/null
                sudo loginctl enable-linger "$remote_user"
            fi
        fi
        test "$(loginctl show-user "$remote_user" --property=Linger --value)" = "yes"
    ' || fail "cannot enable systemd linger for the remote user"
else
    printf '==> Skipping systemd linger setup\n'
fi

printf '==> Preparing remote staging directory\n'
ssh "${SSH_OPTIONS[@]}" "$TARGET" \
    "rm -rf '$REMOTE_APP_DIR/.deploy-next' \
        && mkdir -p '$REMOTE_APP_DIR/.deploy-next/capture_node'"

printf '==> Uploading application source\n'
rsync \
    --archive \
    --delete \
    --rsh "$RSYNC_SSH" \
    --exclude '__pycache__/' \
    --exclude 'web/frontend/node_modules/' \
    src/capture_node/ \
    "$TARGET:$REMOTE_APP_DIR/.deploy-next/capture_node/"

rsync \
    --archive \
    --rsh "$RSYNC_SSH" \
    pyproject.toml \
    README.md \
    config.example.json \
    "$TARGET:$REMOTE_APP_DIR/"

rsync \
    --archive \
    --rsh "$RSYNC_SSH" \
    systemd/ \
    "$TARGET:$REMOTE_APP_DIR/systemd/"

if [[ -n "$WHEELHOUSE" ]]; then
    printf '==> Uploading offline Python wheelhouse\n'
    rsync \
        --archive \
        --delete \
        --rsh "$RSYNC_SSH" \
        "$WHEELHOUSE/" \
        "$TARGET:$REMOTE_APP_DIR/.wheelhouse/"
fi

printf '==> Activating deployment\n'
ssh "${SSH_OPTIONS[@]}" "$TARGET" \
    bash -s -- \
        "$CAMERA_COUNT" \
        "$OFFLINE" \
        "$CACHE_WHEELS" \
        "${CAMERA_SPECS[@]}" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

camera_count="$1"
offline="$2"
cache_wheels="$3"
shift 3
camera_specs=("$@")
app_dir="$HOME/capture-node"
previous_dir="$app_dir/.deploy-previous"
failed_dir="$app_dir/.deploy-failed"
legacy_unit="$HOME/.config/systemd/user/capture-node-web.service"
all_instances=("camera0" "camera1")
instances=("${all_instances[@]:0:camera_count}")
ports=(8000 8001)
legacy_was_enabled=0
legacy_was_active=0
if [[ -f "$legacy_unit" ]]; then
    if systemctl --user is-enabled capture-node-web.service >/dev/null 2>&1; then
        legacy_was_enabled=1
    fi
    if systemctl --user is-active capture-node-web.service >/dev/null 2>&1; then
        legacy_was_active=1
    fi
fi
previously_enabled_instances=()
for instance in "${all_instances[@]}"; do
    if systemctl --user is-enabled "capture-node-web@${instance}.service" \
        >/dev/null 2>&1; then
        previously_enabled_instances+=("$instance")
    fi
done

rollback() {
    status=$?
    trap - ERR
    printf 'Remote activation failed; restoring previous source.\n' >&2
    for instance in "${all_instances[@]}"; do
        systemctl --user stop "capture-node-web@${instance}.service" \
            >/dev/null 2>&1 || true
    done
    rm -rf "$failed_dir"
    if [[ -d "$app_dir/src/capture_node" ]]; then
        mv "$app_dir/src/capture_node" "$failed_dir"
    fi
    if [[ -d "$previous_dir" ]]; then
        mv "$previous_dir" "$app_dir/src/capture_node"
    fi
    for instance in "${previously_enabled_instances[@]}"; do
        systemctl --user enable "capture-node-web@${instance}.service" \
            >/dev/null 2>&1 || true
        systemctl --user restart "capture-node-web@${instance}.service" \
            >/dev/null 2>&1 || true
    done
    if ((legacy_was_enabled == 1)); then
        systemctl --user enable capture-node-web.service >/dev/null 2>&1 || true
    fi
    if ((legacy_was_active == 1)); then
        systemctl --user restart capture-node-web.service >/dev/null 2>&1 || true
    fi
    exit "$status"
}
trap rollback ERR

cd "$app_dir"
[[ -d .deploy-next/capture_node ]]

if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv --system-site-packages .venv
elif python3 -c 'import picamera2' >/dev/null 2>&1 \
    && ! .venv/bin/python -c 'import picamera2' >/dev/null 2>&1; then
    printf 'Enabling Raspberry Pi system camera packages in the virtual environment.\n'
    python3 -m venv --upgrade --system-site-packages .venv
fi

rm -rf "$previous_dir" "$failed_dir"
mkdir -p src
if [[ -d src/capture_node ]]; then
    mv src/capture_node "$previous_dir"
fi
mv .deploy-next/capture_node src/capture_node
rm -rf .deploy-next

runtime_ready() {
    [[ -s .venv/bin/capture-node-web ]] \
        && .venv/bin/python -c \
            'import fastapi, paramiko, PIL, pydantic, uvicorn; import capture_node'
}

if runtime_ready; then
    printf 'Reusing installed runtime dependencies.\n'
elif ((offline == 1)); then
    if ! compgen -G '.wheelhouse/*.whl' >/dev/null; then
        printf 'Offline deployment requires an existing runtime or compatible wheels in %s/.wheelhouse.\n' \
            "$app_dir" >&2
        false
    fi
    printf 'Installing runtime dependencies from the offline wheelhouse.\n'
    .venv/bin/python -m pip install \
        --no-index \
        --find-links "$app_dir/.wheelhouse" \
        -e '.[web,sftp]'
else
    printf 'Installing runtime dependencies from the configured package index.\n'
    .venv/bin/python -m pip install -e '.[web,sftp]'
fi
if ((cache_wheels == 1)); then
    printf 'Caching a compatible remote wheelhouse for later offline deployment.\n'
    .venv/bin/python -m pip wheel \
        --wheel-dir "$app_dir/.wheelhouse" \
        hatchling \
        '.[web,sftp]'
fi
.venv/bin/python -m compileall -q src/capture_node
[[ -s .venv/bin/capture-node-web ]]
.venv/bin/python -c \
    'from capture_node.web.server import main; assert callable(main)'
[[ -s src/capture_node/web/static/index.html ]]

printf 'Preparing %s camera runtime configuration(s).\n' "$camera_count"
.venv/bin/python - "$camera_count" "${camera_specs[@]}" <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from capture_node.core.cameras.options import Picamera2Options, V4L2CameraOptions
from capture_node.core.encodings.options import DngImageOptions
from capture_node.core.maintenance.options import (
    MaintenanceOptions,
    RetentionOptions,
)
from capture_node.core.options import CaptureOptions, Options
from capture_node.core.storages.options import FileStorageOptions

camera_count = int(sys.argv[1])
camera_specs = sys.argv[2:]
app_dir = Path.home() / "capture-node"


def camera_options(spec: str, instance_index: int):
    if spec == "picamera2":
        return Picamera2Options(camera_num=instance_index, width=1920, height=1080)
    if spec.startswith("picamera2:"):
        return Picamera2Options(
            camera_num=int(spec.partition(":")[2]),
            width=1920,
            height=1080,
        )
    if spec.startswith("v4l2:"):
        return V4L2CameraOptions(
            device=Path(spec.partition(":")[2]),
            width=1920,
            height=1080,
        )
    raise ValueError(f"unsupported camera specification: {spec}")


def write_configuration(path: Path, camera_num: int, spec: str) -> None:
    if path.exists():
        Options.model_validate_json(path.read_text(encoding="utf-8"))
        print(f"Preserved configuration: {path.name}")
        return
    options = Options(
        camera=camera_options(spec, camera_num),
        capture=CaptureOptions(mode="disabled"),
        image=DngImageOptions(),
        storages=(
            FileStorageOptions(
                type="file",
                name="local",
                directory=Path(f"captures/camera{camera_num}"),
                layout="daily",
            ),
        ),
        maintenance=MaintenanceOptions(
            retention=(RetentionOptions(target="local", max_age_days=7),),
        ),
    )
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(options.model_dump(mode="json"), ensure_ascii=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    print(f"Created configuration: {path.name} ({spec})")


for camera_num, spec in zip(range(camera_count), camera_specs, strict=True):
    write_configuration(
        app_dir / f"config.camera{camera_num}.json",
        camera_num,
        spec,
    )
PY

find captures -maxdepth 1 -type f -exec mv -n {} captures/camera0/ \;

for index in "${!instances[@]}"; do
    instance="${instances[$index]}"
    port="${ports[$index]}"
    if [[ ! -f "instances/${instance}.env" ]]; then
        {
            printf 'CAPTURE_NODE_CONFIG=%s/config.%s.json\n' "$app_dir" "$instance"
            printf 'CAPTURE_NODE_PORT=%s\n' "$port"
        } >"instances/${instance}.env"
    fi
    chmod 0600 "instances/${instance}.env"
done

touch .env.local
chmod 0600 .env.local

mkdir -p "$HOME/.config/systemd/user"
unit_target="$HOME/.config/systemd/user/capture-node-web@.service"
unit_temporary="${unit_target}.tmp"
install -m 0644 \
    systemd/capture-node-web@.user.service \
    "$unit_temporary"
[[ -s "$unit_temporary" ]]
mv -f "$unit_temporary" "$unit_target"

systemctl --user disable --now capture-node-web.service >/dev/null 2>&1 || true
systemctl --user daemon-reload

diagnose_instance() {
    instance="$1"
    service_name="capture-node-web@${instance}.service"
    printf '\nDiagnostic information for %s\n' "$service_name" >&2
    if [[ -f "instances/${instance}.env" ]]; then
        grep -E '^CAPTURE_NODE_(CONFIG|PORT)=' "instances/${instance}.env" \
            >&2 || true
    fi
    systemctl --user show "$service_name" \
        --property=ActiveState,SubState,Result,ExecMainCode,ExecMainStatus \
        --no-pager >&2 || true
    systemctl --user status "$service_name" --no-pager --lines=20 >&2 || true
    journalctl --user-unit "$service_name" --no-pager --lines=60 >&2 || true
}

for ((index = camera_count; index < ${#all_instances[@]}; index++)); do
    instance="${all_instances[$index]}"
    systemctl --user disable --now "capture-node-web@${instance}.service" \
        >/dev/null 2>&1 || true
done

for instance in "${instances[@]}"; do
    service_name="capture-node-web@${instance}.service"
    systemctl --user enable "$service_name" >/dev/null
    if ! systemctl --user restart "$service_name"; then
        diagnose_instance "$instance"
        false
    fi
done

for index in "${!instances[@]}"; do
    instance="${instances[$index]}"
    port="${ports[$index]}"
    service_name="capture-node-web@${instance}.service"
    health_file="/tmp/capture-node-health-${instance}.json"
    health_error="/tmp/capture-node-health-${instance}.error"
    healthy=0
    for _attempt in {1..30}; do
        if ! systemctl --user is-active "$service_name" >/dev/null 2>&1; then
            break
        fi
        if curl \
            --silent \
            --fail \
            --max-time 2 \
            "http://127.0.0.1:${port}/api/health" \
            >"$health_file" \
            2>"$health_error"; then
            healthy=1
            break
        fi
        sleep 1
    done
    if ((healthy == 0)); then
        printf 'Health check failed: http://127.0.0.1:%s/api/health\n' \
            "$port" >&2
        if [[ -s "$health_error" ]]; then
            printf 'Last curl error: ' >&2
            cat "$health_error" >&2
        fi
        diagnose_instance "$instance"
        false
    fi
    printf '%s: ' "$instance"
    cat "$health_file"
    printf '\n'
    rm -f "$health_file" "$health_error"
done

rm -f "$legacy_unit"
systemctl --user daemon-reload
trap - ERR
rm -rf "$failed_dir"
REMOTE_SCRIPT

host="${TARGET#*@}"
printf '\n==> Deployment completed\n'
for index in "${!INSTANCE_NAMES[@]}"; do
    printf '%s Web: http://%s:%s\n' \
        "${INSTANCE_NAMES[$index]}" \
        "$host" \
        "${INSTANCE_PORTS[$index]}"
done
printf 'Previous source: %s:~/%s/.deploy-previous\n' "$TARGET" "$REMOTE_APP_DIR"
