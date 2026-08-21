# pi-power-manager

`pi-power-manager` is a fail-safe Raspberry Pi 5 RTC wake alarm scheduler. It
only requests an operating-system shutdown after a future RTC alarm has been
written and read back successfully. If the RTC, persistent state, clock
comparison, or ownership check is uncertain, the Pi remains running.

The Core MVP reads an ordered list of powered-on windows from one declarative
JSON file. Daily and weekday recurrence rules, shutdown hooks, packaging, and
Raspberry Pi hardware qualification remain later phases described in the
project documents.

## Power schedule configuration

The default configuration path is `/etc/pi-power-manager/config.json`. The
service reads the complete file again on every 30-second timer run, so an
atomic file replacement takes effect without restarting the timer.

```json
{
  "schema_version": 2,
  "timezone": "Asia/Seoul",
  "minimum_lead_seconds": 60,
  "clock_tolerance_seconds": 5,
  "power_schedule": [
    {
      "power-on": "2026-08-13T08:00:00",
      "power-down": "2026-08-13T12:00:00"
    },
    {
      "power-on": "2026-08-13T13:00:00",
      "power-down": "2026-08-13T18:00:00"
    },
    {
      "power-on": "2026-08-14T08:00:00",
      "power-down": "2026-08-14T18:00:00"
    }
  ]
}
```

Each item is a period during which the Pi should be powered on. At the first
`power-down` (`2026-08-13 12:00`), the manager writes the following item's
`power-on` (`2026-08-13 13:00`) to the RTC, verifies it, and only then requests
shutdown. The same relationship applies to every adjacent pair.

The last `power-down` has no following `power-on`, so the manager deliberately
keeps the Pi running. Add another window when a safe next wake time is known.

Rules:

- `timezone` must be an IANA name such as `Asia/Seoul`.
- Local timestamps without an offset use the configured timezone.
- Timestamps with an explicit offset are also accepted.
- Items must be ordered by `power-on` and must not overlap or touch.
- Unknown keys and ambiguous or nonexistent DST local times are rejected.
- This schedule controls Pi power state, not CaptureNode's internal capture
  timing.

Validate and inspect the file before enabling automatic shutdown:

```bash
sudo pi-power-manager validate-config
sudo pi-power-manager list --json
sudo pi-power-manager dry-run --at 2026-08-13T12:00:00+09:00 --json
```

## Development

Python 3.11 or newer is required. The runtime has no third-party dependencies.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m pi_power_manager --help
```

Use paths under a temporary directory when developing without root privileges:

```bash
mkdir -p /tmp/pi-power-manager/rtc
printf '0\n' > /tmp/pi-power-manager/rtc/wakealarm
date +%s > /tmp/pi-power-manager/rtc/since_epoch
cp examples/config.json /tmp/pi-power-manager/config.json
```

Then inspect the result without changing the RTC or requesting shutdown:

```bash
PYTHONPATH=src python3 -m pi_power_manager \
  --config /tmp/pi-power-manager/config.json \
  --state /tmp/pi-power-manager/state.json \
  --rtc /tmp/pi-power-manager/rtc \
  dry-run --at 2030-01-01T12:00:00+09:00 --json
```

## Safety boundary

`validate-config`, `status`, `diagnose`, `list`, and `dry-run` never request
shutdown. The public `apply` command requires `--yes`; a supplied `--at`
remains a simulation and is rejected for real shutdown. The systemd timer uses
the explicit `tick --execute` path.

Before enabling the timer on a Pi 5:

1. Confirm `/sys/class/rtc/rtc0/wakealarm` is the intended RTC.
2. Confirm the RTC and system clocks are within the configured tolerance.
3. Confirm the bootloader supports RTC wake from low-power halt.
4. Keep external main power connected during low-power standby.
5. Run hardware-in-the-loop wake tests before unattended use.

The provided systemd units are initial deployment artifacts and have not yet
completed Raspberry Pi 5 hardware qualification.
