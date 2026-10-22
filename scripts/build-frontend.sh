#!/usr/bin/env bash
set -Eeuo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
frontend="$project_root/src/pi_node_supervisor/web/frontend"
npm --prefix "$frontend" ci
npm --prefix "$frontend" run build
