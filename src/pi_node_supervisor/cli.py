"""Schema-v2 operator and service entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from pydantic import ValidationError

from .application_v2 import RuntimeControllerV2
from .config_v2 import resolve_configuration
from .errors import SupervisorError
from .power_v2 import PowerPlanV2
from .revision_v2 import RevisionStoreV2
from .runtime_v2 import RuntimeStoreV2
from .service_v2 import SupervisorServiceV2


DEFAULT_STATE = Path("/var/lib/pi-node-supervisor")
DEFAULT_ACTIVE = Path("/etc/pi-node-supervisor/config.json")
DEFAULT_GENERATED_CAPTURE = Path("/var/lib/pi-node-supervisor/capture-nodes")
DEFAULT_STAGING = Path("/var/lib/pi-node-supervisor/staging")
DEFAULT_LOCAL_STORAGE = Path("/var/lib/pi-node-supervisor/storage")
DEFAULT_POWER_CONFIG = Path("/etc/pi-power-manager/config.json")
DEFAULT_POWER_STATE = Path("/var/lib/pi-power-manager/state.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-node-supervisor")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--active-config", type=Path, default=DEFAULT_ACTIVE)
    parser.add_argument(
        "--generated-capture-dir", type=Path, default=DEFAULT_GENERATED_CAPTURE
    )
    parser.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING)
    parser.add_argument(
        "--local-storage-root", type=Path, default=DEFAULT_LOCAL_STORAGE
    )
    parser.add_argument("--power-config", type=Path, default=DEFAULT_POWER_CONFIG)
    parser.add_argument("--power-state", type=Path, default=DEFAULT_POWER_STATE)
    parser.add_argument("--capture-node-executable", type=Path)
    parser.add_argument("--power-manager-executable", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-config")
    validate.add_argument("config", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("config", type=Path)
    plan.add_argument("--power-manager", action="store_true")
    save = sub.add_parser("save-draft")
    save.add_argument("config", type=Path)
    apply = sub.add_parser("apply")
    apply.add_argument("config", type=Path)
    sub.add_parser("apply-draft")
    sub.add_parser("status")
    sub.add_parser("tick")
    sub.add_parser("recover")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--token-env", default="PI_NODE_SUPERVISOR_API_TOKEN")
    return parser


def _read_document(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("configuration root must be a JSON object")
    return value


def _emit(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=lambda item: item.model_dump(mode="json", by_alias=True),
        )
    )


def _timezone() -> tuple[Any, str]:
    zone = datetime.now().astimezone().tzinfo
    if zone is None:
        raise RuntimeError("system local timezone is unavailable")
    name = os.environ.get("TZ")
    if not name:
        try:
            name = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        except OSError:
            name = getattr(zone, "key", None) or str(zone)
    return zone, name


def _service(args: argparse.Namespace) -> SupervisorServiceV2:
    zone, name = _timezone()
    return SupervisorServiceV2(
        RevisionStoreV2(args.state_dir),
        active_configuration_path=args.active_config,
        generated_capture_directory=args.generated_capture_dir,
        power_configuration_path=args.power_config,
        local_timezone=zone,
        timezone_name=name,
    )


def _controller(
    args: argparse.Namespace, service: SupervisorServiceV2
) -> RuntimeControllerV2:
    return RuntimeControllerV2(
        service,
        capture_node_executable=args.capture_node_executable,
        staging_directory=args.staging_dir,
        local_storage_root=args.local_storage_root,
        power_manager_executable=args.power_manager_executable,
        power_manager_state_path=args.power_state,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"validate-config", "plan"}:
            resolution = resolve_configuration(_read_document(args.config))
            if args.command == "validate-config":
                _emit(
                    {
                        "valid": True,
                        "sourceHash": resolution.resolved.source_hash,
                        "warnings": resolution.resolved.warnings,
                    }
                )
            elif args.power_manager:
                zone, name = _timezone()
                _emit(
                    PowerPlanV2(
                        resolution.resolved, local_timezone=zone
                    ).manager_document(datetime.now().astimezone(), timezone_name=name)
                )
            else:
                _emit(
                    {
                        "resolved": resolution.resolved,
                        "captureNodes": resolution.capture_node_configs,
                    }
                )
            return 0

        service = _service(args)
        if args.command == "save-draft":
            _emit(service.save_draft(_read_document(args.config)))
        elif args.command == "apply":
            _emit(service.apply_document(_read_document(args.config)))
        elif args.command == "apply-draft":
            _emit(service.apply_draft())
        elif args.command == "status":
            _emit(
                {
                    "active": service.active(),
                    "revisions": service.store.list_revisions(),
                    "runtime": RuntimeStoreV2(service.store.database_path).counts(),
                }
            )
        elif args.command in {"tick", "recover"}:
            controller = _controller(args, service)
            controller.start()
            try:
                if args.command == "tick":
                    _emit({"items": asyncio.run(controller.tick())})
                else:
                    _emit({"items": controller.runtime.recover_interrupted()})
            finally:
                controller.stop()
        elif args.command == "serve":
            token = os.environ.get(args.token_env)
            if not token:
                parser.error(
                    f"serve requires a non-empty {args.token_env} environment variable"
                )
            try:
                import uvicorn
            except ImportError as exc:
                raise SupervisorError(
                    "Web dependencies are not installed; install pi-node-supervisor[web]"
                ) from exc
            from .api_v2 import create_app_v2

            controller = _controller(args, service)
            uvicorn.run(
                create_app_v2(service, controller, api_token=token),
                host=args.host,
                port=args.port,
            )
        return 0
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
        ValidationError,
        SupervisorError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
