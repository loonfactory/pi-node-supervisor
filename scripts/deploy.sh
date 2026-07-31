#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_APP_DIR="capture-node"
LEGACY_SERVICE_NAME="capture-node-web.service"
SERVICE_TEMPLATE_NAME="capture-node-web@.service"
INSTANCE_NAMES=("camera0" "camera1")
INSTANCE_PORTS=(8000 8001)
SKIP_TESTS=0
SKIP_LINGER=0
DRY_RUN=0
TARGET=""

usage() {
    cat <<'EOF'
Usage: scripts/deploy.sh [options] <user@host>

Build and deploy two CaptureNode Web camera instances to ~/capture-node.
The first deployment creates Camera 0 and Camera 1 configurations on ports
8000 and 8001. Later deployments preserve both configurations, captures/, and
.venv/. systemd linger enables startup without an interactive login.

Options:
  --skip-tests  Skip the local pytest run.
  --skip-linger Skip systemd linger setup when it is provisioned separately.
  --dry-run     Build locally and print the target without connecting.
  -h, --help    Show this help.

Example:
  scripts/deploy.sh huintech@capture-node.local
EOF
}

fail() {
    printf 'deploy: %s\n' "$*" >&2
    exit 1
}

while (($#)); do
    case "$1" in
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

[[ -n "$TARGET" ]] || {
    usage >&2
    exit 2
}
[[ "$TARGET" != *:* ]] || fail "target must be user@host without a remote path"
for command in npm rsync ssh; do
    command -v "$command" >/dev/null || fail "required command is missing: $command"
done

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
        printf 'Instance: %s · port %s\n' \
            "${INSTANCE_NAMES[$index]}" \
            "${INSTANCE_PORTS[$index]}"
    done
    exit 0
fi

SSH_OPTIONS=(
    -o BatchMode=yes
    -o ConnectTimeout=10
)
RSYNC_SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"

printf '==> Checking remote deployment\n'
ssh "${SSH_OPTIONS[@]}" "$TARGET" \
    bash -s -- "$REMOTE_APP_DIR" <<'REMOTE_CHECK' \
    || fail "remote deployment prerequisites are unavailable"
set -u

missing=0

for command_name in curl loginctl python3 rsync systemctl; do
    if ! command -v "$command_name" >/dev/null; then
        printf 'Missing remote command: %s\n' "$command_name" >&2
        missing=1
    fi
done

exit "$missing"
REMOTE_CHECK

printf '==> Preparing remote installation\n'
ssh "${SSH_OPTIONS[@]}" "$TARGET" \
    "mkdir -p \
        '$REMOTE_APP_DIR/captures' \
        '$REMOTE_APP_DIR/captures/camera0' \
        '$REMOTE_APP_DIR/captures/camera1' \
        '$REMOTE_APP_DIR/instances' \
        '$REMOTE_APP_DIR/.deploy-next/capture_node'"

if ! ssh "${SSH_OPTIONS[@]}" "$TARGET" \
    "test -f '$REMOTE_APP_DIR/config.usb.json'"; then
    printf '==> Creating initial Preview-only USB configuration\n'
    rsync \
        --archive \
        --rsh "$RSYNC_SSH" \
        config.usb.example.json \
        "$TARGET:$REMOTE_APP_DIR/config.usb.json"
fi

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
    config.usb.example.json \
    "$TARGET:$REMOTE_APP_DIR/"

rsync \
    --archive \
    --rsh "$RSYNC_SSH" \
    systemd/ \
    "$TARGET:$REMOTE_APP_DIR/systemd/"

printf '==> Activating deployment\n'
ssh "${SSH_OPTIONS[@]}" "$TARGET" \
    bash -s <<'REMOTE_SCRIPT'
set -Eeuo pipefail

app_dir="$HOME/capture-node"
previous_dir="$app_dir/.deploy-previous"
failed_dir="$app_dir/.deploy-failed"
legacy_service="capture-node-web.service"
instances=("camera0" "camera1")
ports=(8000 8001)
had_template_instances=0
if systemctl --user is-enabled capture-node-web@camera0.service \
    >/dev/null 2>&1; then
    had_template_instances=1
fi

rollback() {
    status=$?
    trap - ERR
    printf 'Remote activation failed; restoring previous source.\n' >&2
    for instance in "${instances[@]}"; do
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
    if [[ -x "$app_dir/.venv/bin/python" ]]; then
        "$app_dir/.venv/bin/python" -m pip install --no-deps -e "$app_dir" \
            >/dev/null 2>&1 || true
    fi
    if ((had_template_instances == 1)); then
        for instance in "${instances[@]}"; do
            systemctl --user restart "capture-node-web@${instance}.service" \
                >/dev/null 2>&1 || true
        done
    elif systemctl --user cat "$legacy_service" >/dev/null 2>&1; then
        systemctl --user enable "$legacy_service" >/dev/null 2>&1 || true
        systemctl --user restart "$legacy_service" >/dev/null 2>&1 || true
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

.venv/bin/python -m pip install -e '.[web,sftp]'
.venv/bin/python -m compileall -q src/capture_node

printf 'Preparing Camera 0 and Camera 1 runtime configurations.\n'
.venv/bin/python - <<'PY'
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

app_dir = Path.home() / "capture-node"
source_path = app_dir / "config.usb.json"
source = json.loads(source_path.read_text(encoding="utf-8"))


def write_configuration(path: Path, camera_num: int) -> None:
    if path.exists():
        return
    document = copy.deepcopy(source)
    document["camera"] = {
        "type": "picamera2",
        "camera_num": camera_num,
        "width": 1920,
        "height": 1080,
        "preview_fps": 10,
        "preview_quality": 75,
    }
    for storage in document.get("storages", []):
        if storage.get("type") == "file":
            storage["directory"] = f"captures/camera{camera_num}"
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


write_configuration(app_dir / "config.camera0.json", 0)
write_configuration(app_dir / "config.camera1.json", 1)
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
install -m 0644 \
    systemd/capture-node-web@.user.service \
    "$HOME/.config/systemd/user/capture-node-web@.service"

systemctl --user daemon-reload
systemctl --user disable --now "$legacy_service" >/dev/null 2>&1 || true

for instance in "${instances[@]}"; do
    service_name="capture-node-web@${instance}.service"
    systemctl --user enable "$service_name" >/dev/null
    systemctl --user restart "$service_name"
done

for index in "${!instances[@]}"; do
    instance="${instances[$index]}"
    port="${ports[$index]}"
    service_name="capture-node-web@${instance}.service"
    health_file="/tmp/capture-node-health-${instance}.json"
    healthy=0
    for _attempt in {1..30}; do
        if curl \
            --silent \
            --show-error \
            --fail \
            --max-time 2 \
            "http://127.0.0.1:${port}/api/health" \
            >"$health_file"; then
            healthy=1
            break
        fi
        sleep 1
    done
    if ((healthy == 0)); then
        systemctl --user status "$service_name" --no-pager --lines=20 >&2 || true
        journalctl --user-unit "$service_name" --no-pager --lines=40 >&2 || true
        false
    fi
    printf '%s: ' "$instance"
    cat "$health_file"
    printf '\n'
    rm -f "$health_file"
done

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
