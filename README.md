# CaptureNode

CaptureNode is a reusable Python capture runtime with three layers:

- **Core** schedules still capture, encodes JPEG/PNG, distributes to every
  configured storage target, and emits structured lifecycle events.
- **Headless** runs Core as a single long-lived Linux process configured only
  through JSON.
- **Web** adds a FastAPI API and React operator screen for live preview,
  runtime-safe configuration changes, and recent operation events.

The implementation follows the planning, SRS, design, test-case, and common
development documents under `documents/`. P4 requirements (retry, attempt
history, target-specific retry policy, and additional camera stacks) remain
outside the v0.1.0 milestone.

Features approved and implemented beyond the original document baseline are
tracked in
[`CaptureNode_추가기능명세_v0.1.0.md`](documents/CaptureNode_추가기능명세_v0.1.0.md).

## Project layout

```text
src/capture_node/
├── core/
│   ├── cameras/   # Options, registry, provider, lifecycle, implementations
│   └── storages/  # Options, registry, provider, distribution, implementations
├── headless/      # CLI host
└── web/
    ├── frontend/  # React/Vite source
    └── static/    # Built UI served by FastAPI
```

`CaptureNode` consumes the `CameraProvider` and `StorageProvider` Protocols
exported by the feature packages. `headless` and `web` inject configured
providers from `core`; `core` does not depend on either host.
Camera implementations are selected through immutable registrations rather
than a central conditional factory.
The wheel excludes `web/frontend` and includes the built `web/static` assets.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e '.[web,dev,sftp]'
pytest
```

Build the Web UI:

```bash
npm --prefix src/capture_node/web/frontend install
npm --prefix src/capture_node/web/frontend run build
```

Run against a Raspberry Pi CSI camera:

```bash
capture-node --config config.example.json
capture-node-web --config config.example.json
```

Run against a USB/UVC camera exposed through V4L2:

```bash
capture-node --config config.usb.example.json
capture-node-web --config config.usb.example.json
```

Deploy both Raspberry Pi camera Web instances:

```bash
scripts/deploy.sh huintech@capture-node.local
```

The target is provided at execution time because its hostname or IP address can
change. The script creates `camera0` and `camera1` configurations, separate
capture directories, and systemd template instances on ports 8000 and 8001.
An existing single `config.usb.json` is migrated to Camera 0 and copied as the
initial Camera 1 policy; later deployments preserve both instance
configurations. The script runs the local tests and frontend build, restarts
both services, enables systemd linger, and verifies both `/api/health`
endpoints. The first deployment may request the remote sudo password while
enabling linger. Use `--dry-run` to perform only the local checks. The Raspberry
Pi deployment virtual environment includes system site packages so the
OS-provided Picamera2 and libcamera bindings remain available.

```text
Camera 0 Web: http://device-address:8000
Camera 1 Web: http://device-address:8001
```

The Raspberry Pi adapter imports `picamera2` only when instantiated, keeping
the Core package platform-neutral and testable on x64 hosts. The V4L2 adapter
uses one continuous FFmpeg MJPEG stream for both still capture and Preview.

## SFTP storage

SFTP is an optional Storage implementation that supports the same save, list,
and read contract as local file storage. Configure only the environment
variable name in JSON:

```json
{
  "type": "sftp",
  "name": "archive",
  "host": "storage.example.local",
  "port": 22,
  "username": "capture-node",
  "remote_directory": "/captures",
  "password_env": "CAPTURE_NODE_SFTP_PASSWORD",
  "known_hosts": "/home/huintech/.ssh/known_hosts",
  "connect_timeout_seconds": 10,
  "operation_timeout_seconds": 30
}
```

For deployed Web instances, put a password shared by every instance in the
common file, not in the JSON configuration:

```bash
printf '%s\n' 'CAPTURE_NODE_SFTP_PASSWORD=replace-me' \
  > ~/capture-node/.env.local
chmod 600 ~/capture-node/.env.local
systemctl --user restart capture-node-web@camera0.service
```

The service loads `.env.local` first and then
`instances/<instance>.env`. Therefore an instance file can declare the same
variable to override the common value. The deployment script creates missing
files with mode `0600` and preserves existing contents. Private-key
authentication is available through `private_key`; encrypted keys reference
a passphrase through `private_key_passphrase_env`. SFTP always verifies the
server against the configured `known_hosts` file.
