# CaptureNode Configuration Reference

CaptureNode uses one strict JSON configuration document. The executable example is
[`config.example.json`](../config.example.json). It starts with automatic capture
disabled and local storage only, so copying it does not immediately produce files or
connect to a remote server.

JSON does not support comments. Keep the runnable example small and use the replacement
objects in this document to select a camera, schedule, image format, or storage type.
Unknown properties and invalid combinations are rejected during startup.

## Root object

```json
{
  "camera": {},
  "capture": {},
  "image": {},
  "storages": [],
  "maintenance": {}
}
```

- `camera`: one Picamera2 or V4L2 object.
- `capture`: the automatic capture schedule and camera parameters.
- `image`: one PNG or JPEG encoding policy.
- `storages`: zero or more uniquely named File, SFTP, or FTPS targets.
- `maintenance`: retention checks. Every retention target must match a configured
  storage name.

## Camera

Use Picamera2 for Raspberry Pi CSI cameras:

```json
{
  "type": "picamera2",
  "camera_num": 0,
  "width": 1920,
  "height": 1080,
  "preview_fps": 10,
  "preview_quality": 75
}
```

`camera_num` is zero-based. `width` and `height` must either both be positive integers
or both be omitted, in which case Picamera2 selects its size. `preview_fps` must be
positive and `preview_quality` accepts `1` through `100`.

Use V4L2 for a USB/UVC camera:

```json
{
  "type": "v4l2",
  "device": "/dev/video0",
  "width": 1920,
  "height": 1080,
  "frame_rate": 24,
  "startup_timeout_seconds": 10
}
```

The dimensions, frame rate, and startup timeout must be positive. The adapter requires
FFmpeg and uses `v4l2-ctl` when available to read and apply camera controls. Missing
`v4l2-ctl` disables control inspection without disabling capture or Preview.

## Capture schedule

Interval mode waits one interval after startup and then repeats using a monotonic clock:

```json
{
  "mode": "interval",
  "interval_seconds": 120,
  "daily_times": [],
  "timezone": "Asia/Seoul",
  "params": {}
}
```

`interval_seconds` is stored in seconds and accepts any positive number. The Web UI may
display or edit it as days, hours, minutes, and seconds.

Daily mode captures at one or more unique local times:

```json
{
  "mode": "daily",
  "interval_seconds": null,
  "daily_times": ["08:30:00", "12:00:00", "17:45:30"],
  "timezone": "Asia/Seoul",
  "params": {}
}
```

Each configured `daily_times` value must omit a UTC offset. An empty list, an omitted
field, or `null` keeps Daily mode selected but waits without automatic capture until a
time is configured. `null` is normalized to an empty list. `timezone` must be a valid
IANA timezone name. Disabled mode also keeps Preview and manual capture available but
creates no automatic capture trigger:

```json
{
  "mode": "disabled",
  "interval_seconds": null,
  "daily_times": [],
  "timezone": "Asia/Seoul",
  "params": {}
}
```

`params` contains camera-specific controls applied before capture. Supported names and
ranges depend on the active camera and are exposed by the Web camera controls endpoint.
V4L2 cameras always expose `horizontal_flip` and `vertical_flip`. A matching device
control is preferred; otherwise CaptureNode applies that axis to stored images in
software and the Web mirrors only its displayed Preview. `mirror` is not a separate
parameter because it is equivalent to `horizontal_flip`.

## Image

PNG uses a compression level from `0` through `9`. Compression changes processing time
and file size, not image quality:

```json
{
  "format": "png",
  "compression": 7,
  "embed_metadata": true,
  "raw": {
    "enabled": true,
    "format": "dng"
  }
}
```

JPEG uses a quality value from `1` through `100`:

```json
{
  "format": "jpeg",
  "quality": 90,
  "embed_metadata": true
}
```

When `embed_metadata` is true, CaptureNode writes capture metadata as EXIF data.
`raw.enabled` is supported only by Picamera2. It saves a sensor-native DNG from the
same request and therefore uses the sensor's actual bit depth (10-bit on the deployed
IMX708 and IMX219), rather than promising a fixed 12-bit output.

## Storage

All storage types implement save, read, paged list, and delete. `layout` accepts
`"daily"` or `"flat"`. Daily layout writes a capture as
`yyyyMMdd/<capture filename>` while keeping the logical filename used by the API.

Local file storage:

```json
{
  "type": "file",
  "name": "local",
  "directory": "./captures",
  "layout": "daily"
}
```

Relative paths are resolved from the process working directory. The deployed services
run from `~/capture-node`.

SFTP with password authentication:

```json
{
  "type": "sftp",
  "name": "archive",
  "host": "storage.example.local",
  "port": 22,
  "username": "capture-node",
  "remote_directory": "/captures",
  "private_key": null,
  "password_env": "CAPTURE_NODE_SFTP_PASSWORD",
  "private_key_passphrase_env": null,
  "known_hosts": "~/.ssh/known_hosts",
  "connect_timeout_seconds": 10,
  "operation_timeout_seconds": 30,
  "layout": "daily"
}
```

`remote_directory` must be a normalized absolute POSIX path. SFTP requires
`private_key`, `password_env`, or both. Set `private_key_passphrase_env` only with a
private key. Host verification against `known_hosts` is always enabled.

Explicit FTPS:

```json
{
  "type": "ftps",
  "name": "ftps-archive",
  "host": "storage.example.local",
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

FTPS always uses `AUTH TLS` and protects the data channel. With `verify_tls: true`, omit
`ca_file` for a certificate trusted by the operating system or provide a PEM CA file.
On a physically isolated link only, `verify_tls` may be explicitly set to `false`; a
configured `ca_file` is then retained but ignored. Port `21` is the default. Implicit
FTPS on port `990` is not supported. On the first save, a missing `remote_directory` is
created recursively when the account has permission; each created configured path is
logged once at INFO level.

## Secrets

Configuration files contain environment variable names, never password values. For a
deployed Web service, put shared values in `~/capture-node/.env.local`:

```dotenv
CAPTURE_NODE_SFTP_PASSWORD=replace-me
CAPTURE_NODE_FTPS_PASSWORD=replace-me
```

`instances/cameraN.env` is loaded after `.env.local`, so the same variable in the
instance file takes precedence. Both files must remain mode `0600`. For a manually
started Headless or Web process, export the referenced variables in its environment;
the application does not parse dotenv files itself.

## Maintenance

```json
{
  "interval_seconds": 86400,
  "retention": [
    {
      "target": "local",
      "max_age_days": 7
    }
  ]
}
```

`interval_seconds` and `max_age_days` must be positive. Target names must be unique and
must exist in `storages`. An empty `retention` list disables retention work. A rule can
target remote storage, but the default example deliberately applies deletion only to
`local`.

## Runtime changes

The Web UI persists schedule, image policy, camera selection, and camera control
changes through dedicated endpoints. Storage and maintenance changes require editing
the JSON document and restarting the instance. Existing instance configuration files
are preserved by subsequent deployments.
