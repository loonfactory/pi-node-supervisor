# Pi Node Supervisor

Pi Node Supervisor v2 owns camera and sensor schedules, starts one CaptureNode
process per camera, validates streamed capture bundles, and writes results to
ordered local/SFTP/FTPS targets. CaptureNode no longer owns schedules or storage.

v2 is a clean break. A v1 runtime database is discarded on first startup and
there is no v1 configuration migration or API compatibility layer.

## Configuration

`config.json` is the only device, schedule, storage, and power source of truth.
See [CONFIGURATION.md](CONFIGURATION.md) and [examples/config.json](examples/config.json).

Validate and inspect the resolved camera documents before applying:

```bash
pi-node-supervisor validate-config examples/config.json
pi-node-supervisor plan examples/config.json
pi-node-supervisor plan --power-manager examples/config.json
```

Apply a source document as a new immutable revision:

```bash
pi-node-supervisor \
  --state-dir ./state \
  --active-config ./state/config.json \
  --generated-capture-dir ./state/capture-nodes \
  --power-config ./state/pi-power-manager.json \
  apply examples/config.json
```

Generated CaptureNode documents contain the resolved `captureNode` block. The
Supervisor removes `captures` and `storages` rather than emitting empty arrays.
Capture formats therefore come from `cameras.defaults.captureNode.images` unless
a particular capture request supplies `formats`.

## Run the service

Install the relevant extras and set a non-empty API token:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[web,sensor,remote]'
export PI_NODE_SUPERVISOR_API_TOKEN='replace-me'
.venv/bin/pi-node-supervisor \
  --state-dir ./state \
  --active-config ./state/config.json \
  --generated-capture-dir ./state/capture-nodes \
  --staging-dir ./state/staging \
  --local-storage-root ./state/storage \
  --power-config ./state/pi-power-manager.json \
  --capture-node-executable /path/to/capture-node-web \
  serve --host 127.0.0.1 --port 8080
```

The web client sends `Authorization: Bearer <token>`. Configure storage secrets
through environment variables referenced by `passwordEnv`; never place passwords
in `config.json`.

Local `basePath` values are logical paths below `--local-storage-root`, not
host filesystem destinations. For example, `basePath: "/"` with device path
`rgb` writes below `<local-storage-root>/rgb`; it never writes directly below
the host `/` directory.

## Runtime behavior

- Cameras run in separate CaptureNode processes. Different cameras can capture
  concurrently; requests for the same camera are serialized.
- Every sensor occurrence creates an async task. Access to a shared serial bus
  and append operations for one sensor are serialized.
- A capture response is streamed to temporary staging, checked for request ID,
  capture ID, file count, filename, length, and SHA-256, then written to each
  configured target independently.
- Sensor rows are appended to the remote or local daily CSV using read, temporary
  write, and rename. There is no permanent local primary copy, retry queue,
  pending row, or sync API.
- On startup, runs left in `running` become `interrupted`; they are not replayed.
  Incomplete staging files are not considered valid results.
- Power shutdown preparation is rejected while capture, sensor acquisition, or
  storage work is active. New work is blocked once shutdown draining begins.

## API

Unauthenticated: `GET /api/health`.

Authenticated endpoints:

- `GET|PUT /api/config/draft`
- `GET /api/config/active`
- `POST /api/plan/preview`
- `POST /api/revisions/apply`, `GET /api/revisions`
- `GET /api/power-manager/config`
- `GET /api/dashboard`
- `POST /api/runtime/tick`, `POST /api/runtime/recover`
- `GET /api/runs`, `GET /api/events`, `GET /api/data`
- `GET /api/nodes`
- `GET /api/nodes/{device_id}/configuration`
- `GET /api/nodes/{device_id}/preview`
- `GET /api/nodes/{device_id}/preview/stream`
- `POST /api/captures/manual`
- `POST /api/sensors/collect`

The v1 sensor settings, sensor storage sync, data sync, capture schedule, and
camera storage APIs do not exist in v2.

## Verification

```bash
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider
cd src/pi_node_supervisor/web/frontend && npm run build
```
