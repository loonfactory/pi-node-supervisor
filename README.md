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

For a USB/UVC camera exposed through V4L2, replace the `camera` object with
the V4L2 example in [the configuration reference](documents/configuration.md),
then use the same commands and configuration path. The reference also lists
the schedule, image, storage, secret, and retention alternatives.

Deploy one Raspberry Pi camera Web instance:

```bash
eng/deploy.sh huintech@capture-node.local
```

The target is provided at execution time because its hostname or IP address can
change. The script creates missing remote directories and initial configuration,
preserves operating data on later deployments, enables systemd linger, and verifies
each instance. Use `--bootstrap-ssh` for a new target, repeat `--camera` for explicit
Picamera2 or V4L2 instance setup, and use `--cache-wheels` followed by `--offline` for
an isolated deployment. See the
[deployment guide](documents/deployment.md) for the complete workflow and failure
diagnostics.

```bash
eng/deploy.sh --camera-count 2 huintech@capture-node.local
```

```text
Camera 0 Web: http://device-address:8000
Camera 1 Web: http://device-address:8001
```

The Raspberry Pi adapter imports `picamera2` only when instantiated, keeping
the Core package platform-neutral and testable on x64 hosts. The V4L2 adapter
uses one continuous FFmpeg MJPEG stream for both still capture and Preview.

For a gateway-free wired deployment with a Windows NTP server and Raspberry Pi
RTC, follow the
[isolated network time synchronization guide](documents/operations/isolated-network-time-sync.md).

## Storage layout

File, SFTP, and FTPS storage default to `"layout": "daily"`. Captures are physically
stored as `yyyyMMdd/<existing filename>` using the validated UTC date prefix in
the capture filename. Logical filenames returned by the Web API stay unchanged,
and legacy files in the storage root remain readable and listed. Set
`"layout": "flat"` only when a flat directory is required.

Storage implementations expose save, read, list, and delete operations. File
retention is owned by the separate maintenance policy rather than by `save`.
The example configuration keeps the `local` target for 365 days and runs
maintenance once per day; remote archive targets are not deleted automatically.

PNG and JPEG use independent image settings. PNG defaults to
`"compression": 9`, while JPEG defaults to `"quality": 90`.

## SFTP storage

SFTP is an optional Storage implementation that supports the same save, list,
read, and delete contract as local file storage. Configure only the environment
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
  "operation_timeout_seconds": 30,
  "layout": "daily"
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

## Explicit FTPS storage

FTPS is registered separately from SSH-based SFTP and uses Python's standard
library. It supports explicit TLS on port 21, verifies the server certificate,
and calls `PROT P` so file data and directory listings are encrypted as well as
the control connection.

```json
{
  "type": "ftps",
  "name": "ftps-archive",
  "host": "localhost",
  "port": 21,
  "username": "capture-node",
  "remote_directory": "/captures",
  "password_env": "CAPTURE_NODE_FTPS_PASSWORD",
  "verify_tls": true,
  "ca_file": "~/.config/capture-node/ftps-ca.pem",
  "connect_timeout_seconds": 10,
  "operation_timeout_seconds": 30,
  "layout": "daily"
}
```

`ca_file` can be omitted for certificates issued by a CA already trusted by the
operating system. A self-signed server certificate must be supplied as a PEM CA
file. The configured server must support `AUTH TLS`, protected passive data
connections, and file rename. Listings prefer `MLSD`; servers without it must
support `NLST`, `SIZE`, and `MDTM`.

For a physically isolated wired segment containing only the server and Capture
Node, `"verify_tls": false` may be set explicitly. Any configured `ca_file` is
then retained but ignored, so verification can be restored by changing one
setting. TLS and `PROT P` remain enabled, but the client no longer authenticates
the server. Do not use that setting on a shared LAN or an externally reachable
deployment.

An IIS FTPS server can be provisioned from an elevated 64-bit Windows
PowerShell session:

```powershell
.\eng\deploy-captureftp.ps1 `
  -Password 'replace-with-a-strong-password' `
  -CertIpAddress '192.168.0.10' `
  -AllowedRemoteIp '192.168.0.0/24'
```

Use `-CertIpAddress` when CaptureNode connects by IP so the certificate contains
an IPAddress SAN. The script exports `CaptureFTP-cert.pem` for `ca_file` and
does not report success until certificate validation and passive upload, list,
download, content comparison, and deletion have all succeeded. Keep real
passwords outside the script and rotate any credential that has been committed
or otherwise disclosed.
