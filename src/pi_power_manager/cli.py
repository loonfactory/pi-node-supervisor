from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from .application import Application
from .errors import PowerManagerError, ValidationError
from .power import DryRunPowerAdapter, SystemdPowerAdapter
from .rtc import SysfsRTC
from .storage import JsonStore
from .timeutil import parse_datetime

DEFAULT_CONFIG = Path("/etc/pi-power-manager/config.json")
DEFAULT_STATE = Path("/var/lib/pi-power-manager/state.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-power-manager")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--rtc", type=Path, default=Path("/sys/class/rtc/rtc0"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate-config", help="validate and print the declarative JSON config")
    sub.add_parser("list", help="list configured power windows").add_argument(
        "--json", action="store_true"
    )

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    diagnose_parser = sub.add_parser("diagnose")
    diagnose_parser.add_argument("--json", action="store_true")
    dry_run = sub.add_parser("dry-run")
    dry_run.add_argument("--at", help="ISO 8601 datetime; defaults to now")
    dry_run.add_argument("--json", action="store_true")
    suspend = sub.add_parser("suspend")
    suspend.add_argument("--until", help="ISO 8601 datetime; omit for indefinite suspension")
    sub.add_parser("resume")
    apply_parser = sub.add_parser("apply", help="execute a due managed shutdown")
    apply_parser.add_argument("--yes", action="store_true", help="allow a real systemd power-off request")
    apply_parser.add_argument("--at", help="test-only evaluation time; requires dry-run behavior")
    apply_parser.add_argument("--json", action="store_true")
    tick = sub.add_parser("tick", help="run one scheduler evaluation for systemd")
    tick.add_argument("--execute", action="store_true")
    tick.add_argument("--json", action="store_true")
    return parser


def _application(args: argparse.Namespace, *, execute: bool = False) -> Application:
    store = JsonStore(args.config, args.state)
    rtc = SysfsRTC(args.rtc)
    power = SystemdPowerAdapter() if execute else DryRunPowerAdapter()
    return Application(store, rtc, power)


def _now(value: str | None = None) -> datetime:
    return parse_datetime(value, field="at") if value else datetime.now(UTC)


def _emit(data: Any, json_output: bool = False) -> None:
    if json_output or isinstance(data, (dict, list)):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(data)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        execute = (
            (args.command == "apply" and args.yes and args.at is None)
            or (args.command == "tick" and args.execute)
        )
        app = _application(args, execute=execute)
        if args.command == "validate-config":
            if not args.config.is_file():
                raise ValidationError(f"configuration file not found: {args.config}")
            _emit({"valid": True, "config": app.store.load_config().to_dict()})
        elif args.command == "list":
            config = app.store.load_config()
            _emit(config.to_dict()["power_schedule"], args.json)
        elif args.command == "status":
            _emit(app.status(_now()), args.json)
        elif args.command == "diagnose":
            result = app.diagnose()
            _emit(result, args.json)
            return 0 if result["automatic_shutdown_ready"] else 2
        elif args.command == "dry-run":
            _emit(app.dry_run(_now(args.at)), args.json)
        elif args.command == "suspend":
            state = app.suspend(parse_datetime(args.until, field="until") if args.until else None)
            _emit({"suspended": True, "until": state.to_dict()["suspended_until"]})
        elif args.command == "resume":
            app.resume()
            _emit({"suspended": False})
        elif args.command == "apply":
            if not args.yes:
                parser.error("apply requires --yes to acknowledge the real shutdown risk")
            if args.at is not None:
                _emit({
                    "outcome": "kept_running",
                    "reason": "--at is a simulation input; use dry-run instead of a real shutdown",
                }, args.json)
                return 2
            result = app.apply_due(_now())
            _emit(result, args.json)
            return 0 if result["outcome"] == "shutdown_requested" else 2
        elif args.command == "tick":
            if not args.execute:
                parser.error("tick requires --execute")
            _emit(app.apply_due(_now()), args.json)
        return 0
    except PowerManagerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
