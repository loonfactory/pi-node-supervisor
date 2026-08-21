"""Operator and system integration command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from pydantic import ValidationError

from .adapters import build_adapters
from .errors import SupervisorError
from .executor import ExecutionEngine
from .models import SupervisorConfiguration
from .planner import Planner
from .process_manager import CaptureNodeProcessManager
from .registry import NodeRegistry
from .runtime_store import RuntimeStore
from .service import SupervisorService
from .storage import RevisionStore

DEFAULT_NODES = Path("/etc/pi-node-supervisor/nodes.json")
DEFAULT_STATE = Path("/var/lib/pi-node-supervisor")
DEFAULT_ACTIVE = Path("/etc/pi-node-supervisor/config.json")
DEFAULT_POWER = Path("/etc/pi-power-manager/config.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-node-supervisor")
    parser.add_argument("--nodes", type=Path, default=DEFAULT_NODES)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--active-config", type=Path, default=DEFAULT_ACTIVE)
    parser.add_argument("--power-config", type=Path, default=DEFAULT_POWER)
    parser.add_argument(
        "--capture-node-executable",
        type=Path,
        help="trusted capture-node-web executable used for managed CaptureNode children",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-config", help="validate activities and node contracts")
    validate.add_argument("config", type=Path)

    plan = sub.add_parser("plan", help="render the deterministic derived plan")
    plan.add_argument("config", type=Path)
    plan.add_argument(
        "--power-manager",
        action="store_true",
        help="emit only strict pi-power-manager schema v2 JSON",
    )

    save = sub.add_parser("save-draft", help="validate and atomically save a draft")
    save.add_argument("config", type=Path)

    apply = sub.add_parser("apply", help="validate and activate a configuration revision")
    apply.add_argument("config", type=Path)
    sub.add_parser("apply-draft", help="activate the currently saved draft")
    sub.add_parser("status", help="show local revision state")
    sub.add_parser("tick", help="execute currently due activities once")
    sub.add_parser("recover", help="mark interrupted runs without replaying them")
    sync = sub.add_parser("sync-data", help="index node-owned data metadata")
    sync.add_argument("--node-id")

    serve = sub.add_parser("serve", help="run the authenticated local API")
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
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _service(args: argparse.Namespace) -> SupervisorService:
    return SupervisorService(
        NodeRegistry.load(args.nodes),
        RevisionStore(args.state_dir),
        active_configuration_path=args.active_config,
        power_manager_configuration_path=args.power_config,
    )


def _engine(args: argparse.Namespace, service: SupervisorService) -> ExecutionEngine:
    return ExecutionEngine(
        service.store,
        RuntimeStore(service.store.database_path),
        build_adapters(service.registry),
        power_refresher=service.refresh_recurring_power,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            store = RevisionStore(args.state_dir)
            _emit(
                {
                    **store.status(),
                    "runtime": RuntimeStore(store.database_path).counts(),
                }
            )
            return 0

        if args.command in {"validate-config", "plan"}:
            configuration = SupervisorConfiguration.model_validate(
                _read_document(args.config)
            )
            planner = Planner(NodeRegistry.load(args.nodes))
            plan = planner.plan(configuration)
            if args.command == "validate-config":
                _emit({"valid": True, "source_hash": plan.source_hash})
            elif args.power_manager:
                _emit(planner.power_manager_configuration(configuration, plan))
            else:
                _emit(plan)
            return 0

        service = _service(args)
        if args.command == "save-draft":
            plan = service.save_draft(_read_document(args.config))
            _emit({"saved": True, "source_hash": plan.source_hash})
        elif args.command == "apply":
            _emit(service.apply_document(_read_document(args.config)))
        elif args.command == "apply-draft":
            _emit(service.apply_draft())
        elif args.command == "tick":
            engine = _engine(args, service)
            try:
                _emit({"items": engine.tick()})
            finally:
                engine.close()
        elif args.command == "recover":
            engine = _engine(args, service)
            try:
                _emit({"items": engine.recover()})
            finally:
                engine.close()
        elif args.command == "sync-data":
            engine = _engine(args, service)
            try:
                _emit(engine.sync_data(args.node_id))
            finally:
                engine.close()
        elif args.command == "serve":
            token = os.environ.get(args.token_env)
            if not token:
                parser.error(f"serve requires a non-empty {args.token_env} environment variable")
            try:
                import uvicorn
            except ImportError as exc:
                raise SupervisorError(
                    "Web dependencies are not installed; install pi-node-supervisor[web]"
                ) from exc
            from .api import create_app

            engine = _engine(args, service)
            managed_nodes = [
                node for node in service.registry if node.managed_capture is not None
            ]
            process_manager = None
            if managed_nodes:
                if args.capture_node_executable is None:
                    raise SupervisorError(
                        "managed CaptureNode entries require --capture-node-executable"
                    )
                process_manager = CaptureNodeProcessManager(
                    service.registry,
                    args.capture_node_executable,
                    engine.runtime,
                )

            uvicorn.run(
                create_app(
                    service,
                    api_token=token,
                    engine=engine,
                    manage_worker=True,
                    process_manager=process_manager,
                ),
                host=args.host,
                port=args.port,
            )
        return 0
    except (OSError, json.JSONDecodeError, ValueError, ValidationError, SupervisorError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
