# CaptureNode Deployment

`eng/deploy.sh` deploys one or two independent Web camera instances to
`~/capture-node`. It creates a missing application directory, instance directories,
initial configuration files, the shared secret file, and systemd user services.
Existing configurations, captures, secrets, and the virtual environment are
preserved.

## First Deployment

Use `--bootstrap-ssh` when the target does not have the local SSH public key. An
unknown host key is accepted and recorded, but a changed host key is rejected. The
remote account password may be requested once while `ssh-copy-id` installs the key.

```bash
eng/deploy.sh \
  --bootstrap-ssh \
  --camera picamera2:0 \
  operator@capture-node.local
```

The default public key is `~/.ssh/id_ed25519.pub`. Select another public key with
`--public-key FILE`.

## Camera Instances

Repeat `--camera` in instance order. The first value creates `camera0` on port 8000
and the second creates `camera1` on port 8001.

```bash
eng/deploy.sh \
  --camera v4l2:/dev/v4l/by-id/usb-camera-a-video-index0 \
  --camera v4l2:/dev/v4l/by-id/usb-camera-b-video-index0 \
  operator@capture-node.local
```

Supported initial camera specifications are:

- `picamera2` uses the instance index as the Picamera2 camera number.
- `picamera2:N` uses an explicit Picamera2 camera number.
- `v4l2:/absolute/device/path` uses a USB/UVC V4L2 node. Prefer `/dev/v4l/by-id`.

Camera specifications are used only when `config.cameraN.json` is missing. A later
deployment strictly validates and preserves an existing configuration without rewriting
it. Legacy `capture.timezone`, `image`, and nested `raw` properties are not migrated and
cause deployment validation to fail. Update the configuration to the current schema
before deployment. Use the Web camera settings or edit the configuration intentionally
when changing an installed camera.
The older `--camera-count 1|2` option remains available and creates Picamera2 defaults.

Daily capture times use the operating system's local time. Configuring and maintaining
the host timezone is the installer's responsibility; the deployment script neither sets
nor validates it.

Use `--dry-run` to run local checks and print the resolved instance plan without
connecting to the target.

## Idempotent and Supervisor-managed Deployment

`--skip-if-current` compares a deterministic ID of the deployable Python, Web, package,
and systemd inputs with `~/capture-node/.deployed-source.sha256`. The marker is written
only after activation succeeds. A matching marker skips tests, frontend build, transfer,
service changes, and activation; a missing or different marker performs the complete
deployment.

`--supervisor-managed` installs and validates the shared CaptureNode runtime without
creating, validating, or starting standalone camera instance services. It does not stop
running Supervisor-owned child processes. Pi Node Supervisor uses this mode before its
own atomic activation, then generates camera configurations and owns all CaptureNode
process lifecycles itself.

## Offline Deployment

For a Pi that is online during provisioning and isolated during operation, cache its
architecture- and Python-compatible wheels once:

```bash
eng/deploy.sh --cache-wheels --camera-count 2 operator@capture-node.local
```

Later source deployments can prohibit all remote package-index access:

```bash
eng/deploy.sh --offline --camera-count 2 operator@capture-node.local
```

Offline mode first reuses a complete installed runtime. If dependencies are missing,
it installs only from `~/capture-node/.wheelhouse`. A missing or incomplete wheelhouse
causes activation to fail and the previous source is restored.

For a fresh isolated target, prepare wheels on a matching Raspberry Pi OS, CPU
architecture, and Python version, then copy that directory to the deployment host:

```bash
python3 -m pip wheel --wheel-dir wheelhouse hatchling '.[web,sftp]'
eng/deploy.sh --offline --wheelhouse wheelhouse operator@capture-node.local
```

Do not prepare binary wheels on an x64 development machine for an ARM Raspberry Pi.

## Failure Diagnostics

Health probes do not print a connection error for every retry. If an instance exits or
does not become healthy, deployment prints the configuration path and port, systemd
result and exit status, service status, and the latest journal entries. Activation then
restores the previous application source and restarts the previously enabled instances.
