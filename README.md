# pi-node-supervisor

`pi-node-supervisor` turns purpose-oriented capture, sensor, and maintenance
activities into deterministic execution and Raspberry Pi power plans. It is a
local, offline-first boundary between an operator and managed programs such as
CaptureNode and `pi-power-manager`.

The current implementation covers the complete local CaptureNode path:

- strict schema v1 activity and node-registry validation;
- unambiguous IANA timezone handling, including DST gap/fold rejection;
- node capability, profile, runtime, preparation, and conflict validation;
- deterministic activity timelines and explainable power-window merging;
- strict `pi-power-manager` schema v2 output;
- atomic Draft storage and immutable SQLite-backed revisions;
- rollback of active and power-manager files when activation fails;
- restricted CaptureNode HTTP execution with per-request timeouts;
- Supervisor-owned CaptureNode child-process startup, monitoring, and restart;
- idempotent due-work claiming and persisted run results;
- restart recovery that marks active runs interrupted without replaying them;
- structured event history and a non-owning CaptureNode data catalog;
- an authenticated local API, equivalent CLI, and responsive operator Web UI.

CaptureNode Core still owns image capture and image originals, while the
Supervisor can own the lifecycle of its local Web runtime. `pi-power-manager`
still owns RTC access and the real shutdown request. The Supervisor does not
execute arbitrary shell strings or acquire either privilege.

## Development

Python 3.11 or newer is required.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[web,sensor,remote,dev]'
.venv/bin/pytest
```

The `sensor` extra installs `pyserial`. HUINTECHDEVICE is currently a
source-only Python library, so its `python` directory must also be present on
`PYTHONPATH` (or be packaged by that project) when the CWT adapter runs. The
Supervisor imports only its public CWT driver API and does not run a separate
HUINTECHDEVICE service.

The live HTTP integration test starts the real CaptureNode FastAPI app on a
loopback socket. Environments that prohibit sockets skip that one test; run it
where loopback is allowed with:

```bash
.venv/bin/pytest tests/test_capture_node_integration.py
```

Without installing the project, the planner can be run directly:

```bash
PYTHONPATH=src python3 -m pi_node_supervisor \
  --nodes examples/nodes.dual-camera-sensor.json \
  plan examples/config.json
```

Render only the file accepted by the current `pi-power-manager`:

```bash
PYTHONPATH=src python3 -m pi_node_supervisor \
  --nodes examples/nodes.json \
  plan examples/config.json --power-manager
```

The rendered document deliberately contains only `schema_version`,
`timezone`, `minimum_lead_seconds`, `clock_tolerance_seconds`, and
`power_schedule`. Explanations and activity IDs remain in the Supervisor plan
because `pi-power-manager` rejects unknown keys.

The preview also marks whether each `power-down` has a later `power-on` that
can be written to the RTC. The final finite window has no such wake time, so it
is explicitly shown as unable to shut down automatically; this matches
`pi-power-manager`'s fail-safe behavior.

## Safe local activation

Use temporary paths during development:

```bash
PYTHONPATH=src python3 -m pi_node_supervisor \
  --nodes examples/nodes.json \
  --state-dir /tmp/pi-node-supervisor/state \
  --active-config /tmp/pi-node-supervisor/config.json \
  --power-config /tmp/pi-node-supervisor/pi-power-manager.json \
  apply examples/config.json
```

Activation writes an immutable revision first, snapshots both external files,
then atomically replaces the power-manager and active Supervisor files. A
failure restores both snapshots and records a failed revision instead of
changing the active revision.

Production defaults follow the product plan:

```text
/etc/pi-node-supervisor/config.json
/etc/pi-node-supervisor/nodes.json
/var/lib/pi-node-supervisor/state.db
/var/lib/pi-node-supervisor/revisions/
/etc/pi-power-manager/config.json
```

## Local API

The API requires a token on every endpoint except health and binds to loopback
by default. Keep the token outside JSON configuration.

```bash
export PI_NODE_SUPERVISOR_API_TOKEN='replace-with-a-local-secret'
PYTHONPATH=src python3 -m pi_node_supervisor \
  --nodes examples/nodes.json \
  --state-dir /tmp/pi-node-supervisor/state \
  --active-config /tmp/pi-node-supervisor/config.json \
  --power-config /tmp/pi-node-supervisor/pi-power-manager.json \
  serve
```

Open `http://127.0.0.1:8080/`, enter the same token, and use the dashboard to
configure CaptureNode-compatible interval/daily shooting through the
operator-friendly Settings screen, generate and edit daily sensor collection
times with a default 00:00–23:30/30-minute range, preview the derived RTC power windows,
configure each camera's file/SFTP/FTPS storage, evaluate due work, inspect node
health, browse run/event history, and reindex node data. Advanced JSON editing
remains available behind a disclosure for activity types that do not yet have
a form. The browser keeps the token only in `sessionStorage`; configuration,
events, and catalog rows never store it.

For another machine on a trusted wired LAN, pass an explicit `--host` address.
The built-in server does not terminate TLS and must not be exposed to the
Internet.

The advanced configuration accepts CaptureNode-native values without changing
their field names. `capture_node_settings.common` is recursively merged into
every managed camera and `capture_node_settings.nodes.<node-id>` is merged on
top of it. Objects are merged recursively; arrays and scalar values replace the
common value. `GET /api/settings/capture-nodes` returns the current native
configuration of every managed CaptureNode in the same shape, so it can be
copied into the advanced editor. For example, a shared 180-degree camera mount
can be represented as:

```json
{
  "capture_node_settings": {
    "common": {
      "capture": {
        "params": {
          "horizontal_flip": true,
          "vertical_flip": true
        }
      }
    },
    "nodes": {}
  }
}
```

Node-specific CaptureNode configuration, including calibration metadata, can
instead be placed below `capture_node_settings.nodes.<node-id>`.

Authenticated endpoints:

- `PUT /api/config/draft`
- `GET /api/config/draft`
- `GET /api/config/active`
- `GET /api/settings/capture-nodes`
- `POST /api/plan/preview`
- `POST /api/revisions/apply`
- `GET /api/revisions`
- `GET /api/power-manager/config`
- `GET /api/dashboard`
- `POST /api/runtime/tick`
- `POST /api/runtime/recover`
- `GET /api/runs`
- `GET /api/events`
- `GET /api/data`
- `POST /api/data/sync`
- `GET /api/sensor-data`
- `GET /api/sensor-storage`
- `POST /api/sensor-storage/sync`
- `GET /api/sensor-settings`
- `PUT /api/sensor-settings/{node_id}`
- `GET /api/sensor-schedules`
- `PUT /api/sensor-schedules`
- `GET /api/nodes`
- `GET /api/nodes/{node_id}/configuration`
- `GET /api/capture-schedules`
- `PUT /api/capture-schedules`
- `PUT /api/nodes/{node_id}/storages`
- `PUT /api/settings/cameras/storages`
- `GET /api/nodes/{node_id}/preview`
- `GET /api/nodes/{node_id}/preview/stream`

Pass the token as `X-Supervisor-Token`. The token is an initial local API
boundary, not the final Viewer/Operator/Administrator session system described
in the broader product plan.

## CaptureNode execution and recovery

Set `kind` to `capture-node` and provide its local HTTP endpoint in
`nodes.json`. The endpoint accepts only HTTP(S) URLs without embedded
credentials, query strings, fragments, or markup characters.

```json
{
  "id": "camera0",
  "kind": "capture-node",
  "activity_types": ["capture"],
  "profiles": ["default"],
  "endpoint": "http://127.0.0.1:8000",
  "request_timeout_seconds": 10,
  "completion_timeout_seconds": 60
}
```

At the due time the executor atomically claims `(revision, activity)` in
SQLite, checks `/api/health`, applies the activity's validated `parameters`
through `/api/config`, calls `/api/capture`, and waits for a matching terminal
event from `/api/events`. A successful capture remains successful even if
metadata indexing later fails; the indexing error is recorded separately and
can be retried.

One-shot work more than 60 seconds late is recorded as `missed` and is not
replayed. On process start, any run left as `running` is changed to
`interrupted`, with `replayed: false` in its recovery event. This avoids
duplicating a capture whose external outcome cannot be proven after a crash.

For a managed CaptureNode, add `managed_capture` with an absolute config path,
loopback host/port, and optional working directory. Start the Supervisor with
one trusted `--capture-node-executable`; registry JSON cannot select arbitrary
executables or shell commands. The Supervisor starts these children before its
execution worker, waits for camera health, terminates them only after accepted
work drains, and restarts unexpected exits with persisted lifecycle events.

Manual runtime commands use the same engine as the Web service:

```bash
pi-node-supervisor tick
pi-node-supervisor recover
pi-node-supervisor sync-data --node-id camera0
pi-node-supervisor status
```

The data catalog stores only metadata and the CaptureNode-owned read URI. It
does not copy, move, transform, or delete source images. A full reindex walks
all pages returned by each CaptureNode storage target.

Storage changes are validated against the CaptureNode storage contract, then
written atomically. Managed camera children are restarted and checked for
readiness; if the new configuration cannot start, the exact previous file is
restored and the children are started again. Removing a storage also removes
retention rules that target that storage. Remote-storage passwords are never
accepted as values by this API: settings contain only protected environment
variable names (or an SFTP private-key path), and the secret itself belongs in
the device's mode-`0600` environment file.

Automatic capture settings use the same three modes as CaptureNode:
`disabled`, positive fractional-second `interval`, and timezone-aware `daily`
with any number of unique wall-clock times. CaptureNode's native scheduler
executes recurring captures; Supervisor never turns a due recurrence into an
HTTP capture command. Supervisor stores the same validated setting solely to
derive the RTC power plan. The two configurations are applied as one
coordinated operation and CaptureNode's previous file is restored if the power
revision cannot be activated. Daily times produce boot/capture/shutdown
windows. Interval mode stays continuously powered because CaptureNode's
process-relative timer restarts when the child process restarts. The finite
168-hour RTC schedule is refreshed hourly, and a refresh failure leaves the
final window unable to shut down rather than risk missing the next wake-up.
One-shot dated work remains available under the advanced disclosure.

The initial operating defaults are daily camera captures from `05:00` through
`20:00` at 30-minute intervals, daily sensor collection from `00:00` through
`23:30` at 30-minute intervals, and independent power-keep windows at
`08:50`-`09:30` and `17:20`-`18:10` in `Asia/Seoul`. Existing active
configurations are never replaced by these defaults during an upgrade.

Sensor collection schedules store the generated wall-clock `daily_times` as
their execution source. The 30-minute interval is only the initial form value;
operators may generate any number of times or edit individual seconds. Each
occurrence receives a deterministic ID, is claimed once per revision, and is
marked missed instead of being measured retroactively after the lateness
limit. The same rolling refresh updates both the executable occurrence plan
and the RTC power file.

Configure a CWT node in `nodes.json` with a fixed driver, an allowed absolute
serial path (`/dev/ttyAMA*`, `/dev/ttyUSB*`, `/dev/ttyACM*`, or
`/dev/serial/by-id/*`), baud rate, timeout, and Modbus slave ID. The adapter
keeps one serial handle for the Supervisor lifecycle and requests only
`HUMIDITY | TEMPERATURE` in one driver call. Conductivity, salinity, TDS, and
all write APIs are outside this boundary.

A read-only hardware smoke check is available after granting the service user
serial-port access. HUINTECHDEVICE keeps its Python package below `python/`,
so use that directory on `PYTHONPATH`:

```bash
PYTHONPATH=src:../huintech-device/huintech-device-main/python .venv/bin/python \
  scripts/read_cwt_sensor.py --port /dev/ttyAMA0 --slave-id 1
```

The command prints only `observed_at`, `temperature_c`, and
`humidity_percent`, then closes the port. It never invokes calibration,
factor, offset, or slave-ID writes.

Sensor measurements are committed to the local authoritative CSV configured
by `sensor_storage.local_directory`. The relative path is
`<node-id>/YYYY/MM/YYYY-MM-DD.csv`, and the public file has exactly
`timestamp,temperature_c,humidity_percent`. `timestamp` is the scheduled slot
with its UTC offset, so an execution delay or retry cannot create a new row.
There is no fixed daily row limit; the number follows the stored schedule.

Each configured file/SFTP/FTPS mirror receives the complete local CSV under
the same relative path. Uploads use a temporary name in the destination
directory and then rename it to the final name; the remote file is never
appended. Delivery hashes, attempts, and exponential retry times are stored in
Supervisor SQLite, while temperature and humidity values remain only in CSV.
Remote failures leave the local collection successful and are retried on
worker startup or data synchronization. `/api/sensor-storage` reports pending,
failed, and successful deliveries. Passwords remain environment-variable
references and are never written into state or events.

The Sensor Web screen reads values directly from the authoritative CSV through
`/api/sensor-data`; measurements are not copied into SQLite. It displays only
the scheduled timestamp, temperature, humidity, local path, and file-level
mirror state. The Settings screen stages strict CWT serial and sensor storage
changes in `nodes.json`, first preserving `nodes.json.previous`. A running
adapter continues using the previous settings until the service is restarted.

Managed CaptureNode children emit structured JSON logs. Supervisor consumes
that child-process log stream and stores `capture-node.*` records in its event
catalog, so native timer captures remain observable across CaptureNode restarts
without polling the capture execution API.

Both capture and storage controls default to `common`, applying the same
settings to every managed camera. Selecting a named camera explicitly creates
an individual application instead. Common storage paths may contain
`{camera}`, which is replaced with each node ID before the CaptureNode configs
are atomically restarted and checked.

## systemd

[`systemd/pi-node-supervisor.service`](systemd/pi-node-supervisor.service)
runs under a dedicated unprivileged user. Install the package under
`/opt/pi-node-supervisor`, create the configuration/state directories, and put
the API token in `/etc/pi-node-supervisor/supervisor.env` with mode `0600`.
Grant that service account write access only to the Supervisor paths and the
specific `/etc/pi-power-manager/config.json` file; it does not need RTC or
shutdown capabilities.

### Raspberry Pi user-service deployment

[`scripts/deploy.sh`](scripts/deploy.sh) builds a wheel, checks the SSH target
and registered CaptureNode health, installs an isolated versioned virtual
environment, preserves the active schedule and previous unit, and activates a
user-level systemd service. A failed health check automatically restores the
previous unit/release. The default target and registry match `hu-pi-02` with
CaptureNode on ports 8000 and 8001:

```bash
scripts/deploy.sh
```

Override values without editing the script:

```bash
scripts/deploy.sh \
  --target huintech@192.168.12.7 \
  --preserve-existing-nodes \
  --nodes examples/nodes.json \
  --initial-config examples/config.empty.json \
  --huintech-device-source ../huintech-device/huintech-device-main/python \
  --bind-host 0.0.0.0 \
  --port 8080
```

Use `--preserve-existing-nodes` for an in-place upgrade when the target's
`config/nodes.json` is already authoritative. The file stays on the target,
is validated by the new release, and is neither uploaded nor overwritten.

When the registry contains a CWT sensor, deployment packages the public
HUINTECHDEVICE Python source into the versioned release, installs the
`sensor`/`remote` extras, verifies the driver import, and confirms that the
service user can read and write the configured serial device before switching
`current`. Grant serial access once, then start a new login session before
deploying:

```bash
sudo usermod -aG dialout huintech
id huintech
test -r /dev/ttyAMA0 && test -w /dev/ttyAMA0
```

Remote SFTP/FTPS passwords stay only in the protected
`config/supervisor.env`, for example
`SENSOR_STORAGE_PASSWORD=...`; the browser stores only that environment
variable name. If activation fails, the deploy script restores `previous`, the
prior systemd unit, and the backed-up nodes file. For a manual rollback, stop
the service, point `current` to the verified `previous` release, restore the
corresponding `backups/nodes.json.*`, reload user systemd, and restart the
service.

The default deployment replaces the legacy `capture-node-web@camera0/1` user
services. It preserves managed copies of both existing camera configs and, on
first install, imports their native schedules as the initial RTC power plan.
It then starts and monitors the same trusted CaptureNode runtime as child
processes. Recurring capture remains owned by each copy's native CaptureNode timer. Use
`--keep-external-capture-nodes` only when the registry does not contain managed
children.

The first installation activates an empty schedule. Later deployments never
replace `config/config.json`, the SQLite state, or the API token. The actual
`pi-power-manager` configuration is also left untouched: generated power JSON
is staged below the Supervisor installation until privileged integration is
explicitly configured. The token remains mode `0600` at
`~/pi-node-supervisor/config/supervisor.env`.

Install the privileged RTC boundary separately. This command installs
`pi-power-manager`, grants Supervisor write access only to its dedicated
configuration directory, and redeploys Supervisor against that path. It
deliberately leaves the automatic shutdown timer disabled and inactive:

```bash
scripts/install-power-manager.sh --target huintech@192.168.12.7
```

Administrator authentication may be requested over SSH. Confirm diagnostics
and complete a supervised wake test before explicitly enabling shutdown:

```bash
scripts/install-power-manager.sh --target huintech@192.168.12.7 --enable
```

The enable path runs hardware, bootloader, RTC, clock, configuration, and state
diagnostics first. Any failed check keeps the timer disabled. Supervisor never
receives RTC-write or shutdown permission; only the root system service owns
those operations.

Automatic power management also has a physical active-low interlock on BCM
GPIO26 (physical pin 37). At startup, a dedicated service configures it as an
input with an internal pull-up, reads it once, and latches that mode for the
rest of the boot:

- GPIO26 jumpered directly to GND reads LOW and skips `pi-power-manager`;
- removing the jumper reads HIGH and permits automatic RTC/shutdown handling;
- setup failures, read failures, and unknown states latch the blocked mode.

The power service's `ExecCondition` reads only the boot latch, so changing the
jumper after startup has no effect until the next boot (or an administrator
explicitly restarts `pi-power-manager-interlock.service`). LOW prevents the
privileged application from running at all. Never configure GPIO26 as an output
while it is connected to GND. The timer remains disabled unless installation
is explicitly invoked with `--enable`.

## Remaining boundary

RTC alarm writes and actual system shutdown remain exclusively owned by
`pi-power-manager`. Role-based user accounts, HTTPS termination,
retention/deletion workflows, and multi-Pi control remain later work. The
current API token gives one local operator identity rather than the planned
Viewer/Operator/Administrator split.

The Web UI camera view consumes authenticated MJPEG streams from managed
CaptureNodes. The browser opens each stream with `X-Supervisor-Token`, parses
bounded JPEG/PNG multipart frames, and closes all streams when the operator
leaves the camera view. Camera ports remain bound to loopback and the API token
is never placed in a URL. The single-frame endpoint remains available for
health checks and low-bandwidth clients.

## New Raspberry Pi bootstrap

Provision a Raspberry Pi 5 from SSH key enrollment through CaptureNode,
Supervisor, and the privileged RTC boundary with one interactive command:

```bash
scripts/bootstrap-device.sh scnu@192.168.12.8
```

The script installs the local public key when needed, installs Raspberry Pi
runtime packages, applies `POWER_OFF_ON_HALT=1` and `WAKE_ON_GPIO=0`, reboots,
deploys two Picamera2 instances, renders account-specific managed-node paths,
and installs `pi-power-manager`. Automatic shutdown remains disabled. Remove
the GPIO26-to-GND jumper and opt in only after checking the device:

```bash
scripts/bootstrap-device.sh --enable-power scnu@192.168.12.8
```

`--enable-power` fails and disables the timer if the startup GPIO26 latch is
not HIGH. Repeat `--camera` to select one or two Picamera2 or V4L2 devices.
