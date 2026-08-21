"""Idempotent due-work execution and fail-safe restart recovery."""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .adapters import ManagedNodeAdapter, PreviewByteStream
from .models import ActivityPlan, RunRecord
from .runtime_store import RuntimeStore
from .storage import RevisionStore


class ExecutionEngine:
    def __init__(
        self,
        revisions: RevisionStore,
        runtime: RuntimeStore,
        adapters: dict[str, ManagedNodeAdapter],
        *,
        allowed_lateness_seconds: int = 60,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        power_refresher: Callable[[datetime], None] | None = None,
    ) -> None:
        if allowed_lateness_seconds < 0:
            raise ValueError("allowed_lateness_seconds must not be negative")
        self.revisions = revisions
        self.runtime = runtime
        self.adapters = adapters
        self.allowed_lateness_seconds = allowed_lateness_seconds
        self.clock = clock
        self.power_refresher = power_refresher
        self._tick_lock = threading.Lock()
        self._native_sync_lock = threading.Lock()
        self._native_sync_failures: set[str] = set()

    def recover(self) -> list[RunRecord]:
        return self.runtime.recover_interrupted()

    def tick(self, now: datetime | None = None) -> list[RunRecord]:
        evaluation_time = (now or self.clock()).astimezone(UTC)
        with self._tick_lock:
            previous_bundle = self.revisions.active_bundle()
            if self.power_refresher is not None:
                try:
                    self.power_refresher(evaluation_time)
                except Exception as exc:
                    self.runtime.append_event(
                        "power.plan_refresh_failed",
                        severity="error",
                        payload={"error": f"{type(exc).__name__}: {exc}"},
                    )
            bundle = self.revisions.active_bundle()
            if bundle is None:
                return []
            revision_id, _configuration, plan = bundle
            plans = {activity.activity_id: activity for activity in plan.activities}
            if previous_bundle is not None and previous_bundle[0] == revision_id:
                for activity in previous_bundle[2].activities:
                    plans.setdefault(activity.activity_id, activity)
            activities = [
                activity
                for activity in plans.values()
                if activity.activity_type not in {"capture-schedule", "power-keep"}
            ]
            outcomes: list[RunRecord] = []
            for activity in sorted(
                activities, key=lambda item: (item.requested_start, item.activity_id)
            ):
                outcome = self._consider_activity(
                    revision_id, activity, evaluation_time
                )
                if outcome is not None:
                    outcomes.append(outcome)
            return outcomes

    def _consider_activity(
        self, revision_id: int, activity: ActivityPlan, now: datetime
    ) -> RunRecord | None:
        if now < activity.requested_start:
            return None
        latest = activity.requested_start + timedelta(
            seconds=self.allowed_lateness_seconds
        )
        if now > latest:
            reason = (
                f"activity was {int((now - activity.requested_start).total_seconds())}s late; "
                "one-shot automatic replay is disabled"
            )
            run = self.runtime.claim_run(
                revision_id=revision_id,
                activity_id=activity.activity_id,
                node_id=activity.node_id,
                scheduled_at=activity.requested_start,
                status="missed",
                error=reason,
            )
            if run is not None:
                self.runtime.append_event(
                    "run.missed",
                    severity="warning",
                    run_id=run.run_id,
                    activity_id=activity.activity_id,
                    node_id=activity.node_id,
                    payload={"reason": reason, "replayed": False},
                )
            return run

        run = self.runtime.claim_run(
            revision_id=revision_id,
            activity_id=activity.activity_id,
            node_id=activity.node_id,
            scheduled_at=activity.requested_start,
        )
        if run is None:
            return None
        self.runtime.append_event(
            "run.started",
            run_id=run.run_id,
            activity_id=activity.activity_id,
            node_id=activity.node_id,
            payload={"revision_id": revision_id},
        )

        adapter = self.adapters.get(activity.node_id)
        if adapter is None:
            if activity.activity_type == "maintenance-window" and activity.node_id == "supervisor":
                result = {"mode": "maintenance-window", "kept_powered": True}
                completed = self.runtime.complete_run(
                    run.run_id, status="succeeded", result=result
                )
                self.runtime.append_event(
                    "run.succeeded",
                    run_id=run.run_id,
                    activity_id=activity.activity_id,
                    node_id=activity.node_id,
                    payload=result,
                )
                return completed
            error = f"no executable adapter is registered for node {activity.node_id}"
            failed = self.runtime.complete_run(
                run.run_id, status="failed", error=error
            )
            self.runtime.append_event(
                "run.failed",
                severity="error",
                run_id=run.run_id,
                activity_id=activity.activity_id,
                node_id=activity.node_id,
                payload={"error": error},
            )
            return failed

        try:
            result = adapter.execute(activity, run.run_id)
            external_id = result.get("capture_id")
            indexed = 0
            data_index_error = None
            try:
                records = adapter.list_data(
                    activity_id=activity.activity_id,
                    run_id=run.run_id,
                    capture_id=external_id if isinstance(external_id, str) else None,
                )
                for record in records:
                    self.runtime.upsert_data(record)
                    indexed += 1
            except Exception as exc:
                data_index_error = f"{type(exc).__name__}: {exc}"
                self.runtime.append_event(
                    "data.index_failed",
                    severity="error",
                    run_id=run.run_id,
                    activity_id=activity.activity_id,
                    node_id=activity.node_id,
                    payload={"error": data_index_error},
                )
            result = {**result, "indexed_data_count": indexed}
            if data_index_error is not None:
                result["data_index_error"] = data_index_error
            completed = self.runtime.complete_run(
                run.run_id,
                status="succeeded",
                external_id=external_id if isinstance(external_id, str) else None,
                result=result,
            )
            self.runtime.append_event(
                "run.succeeded",
                run_id=run.run_id,
                activity_id=activity.activity_id,
                node_id=activity.node_id,
                payload=result,
            )
            return completed
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failed = self.runtime.complete_run(
                run.run_id, status="failed", error=error
            )
            self.runtime.append_event(
                "run.failed",
                severity="error",
                run_id=run.run_id,
                activity_id=activity.activity_id,
                node_id=activity.node_id,
                payload={"error": error},
            )
            return failed

    def sync_data(self, node_id: str | None = None) -> dict[str, Any]:
        with self._native_sync_lock:
            return self._sync_data(node_id)

    def _sync_data(self, node_id: str | None = None) -> dict[str, Any]:
        selected = (
            {node_id: self.adapters[node_id]}
            if node_id is not None and node_id in self.adapters
            else self.adapters
            if node_id is None
            else {}
        )
        if node_id is not None and not selected:
            raise ValueError(f"node has no data adapter: {node_id}")
        indexed = 0
        new_data = 0
        runs_added = 0
        failures: list[dict[str, str]] = []
        active_bundle = self.revisions.active_bundle()
        revision_id = active_bundle[0] if active_bundle is not None else None
        for current_node, adapter in selected.items():
            try:
                node_indexed = 0
                node_new_data = 0
                node_runs_added = 0
                for record in adapter.list_data():
                    linked_record, created = self._link_native_record(
                        record, revision_id=revision_id
                    )
                    if self.runtime.get_data(linked_record.data_id) is None:
                        new_data += 1
                        node_new_data += 1
                    self.runtime.upsert_data(linked_record)
                    indexed += 1
                    node_indexed += 1
                    node_runs_added += int(created)
                    runs_added += int(created)
                if node_new_data or node_runs_added:
                    self.runtime.append_event(
                        "data.indexed",
                        node_id=current_node,
                        payload={
                            "count": node_indexed,
                            "new_data": node_new_data,
                            "runs_added": node_runs_added,
                            "automatic": True,
                        },
                    )
            except Exception as exc:
                failures.append({"node_id": current_node, "error": str(exc)})
                self.runtime.append_event(
                    "data.index_failed",
                    severity="error",
                    node_id=current_node,
                    payload={"error": str(exc)},
                )
        return {
            "indexed": indexed,
            "new_data": new_data,
            "runs_added": runs_added,
            "failures": failures,
        }

    def sync_native_captures(self) -> dict[str, Any]:
        """Import CaptureNode timer completions without executing captures here."""

        with self._native_sync_lock:
            return self._sync_native_captures()

    def _sync_native_captures(self) -> dict[str, Any]:

        active_bundle = self.revisions.active_bundle()
        if active_bundle is None:
            return {"runs_added": 0, "data_indexed": 0, "failures": []}
        revision_id = active_bundle[0]
        runs_added = 0
        data_indexed = 0
        failures: list[dict[str, str]] = []
        for node_id, adapter in self.adapters.items():
            event_reader = getattr(adapter, "capture_events", None)
            if not callable(event_reader):
                continue
            try:
                events = event_reader()
                for event in sorted(events, key=lambda item: str(item.get("timestamp", ""))):
                    event_type = event.get("event_type")
                    capture_id = event.get("capture_id")
                    if (
                        event_type not in {"terminal.completed", "terminal.discarded", "capture.failed"}
                        or not isinstance(capture_id, str)
                        or not capture_id
                        or self.runtime.find_run_by_external_id(node_id, capture_id) is not None
                    ):
                        continue
                    occurred_at = self._event_time(event)
                    activity_id = self._native_activity_id(node_id, capture_id)
                    run = self.runtime.claim_run(
                        revision_id=revision_id,
                        activity_id=activity_id,
                        node_id=node_id,
                        scheduled_at=occurred_at,
                    )
                    if run is None:
                        continue
                    if event_type == "terminal.completed":
                        data_index_error = None
                        try:
                            records = adapter.list_data(
                                activity_id=activity_id,
                                run_id=run.run_id,
                                capture_id=capture_id,
                            )
                            for record in records:
                                self.runtime.upsert_data(record)
                                data_indexed += 1
                        except Exception as exc:
                            records = []
                            data_index_error = f"{type(exc).__name__}: {exc}"
                            self.runtime.append_event(
                                "data.index_failed",
                                severity="error",
                                run_id=run.run_id,
                                activity_id=activity_id,
                                node_id=node_id,
                                payload={"error": data_index_error},
                            )
                        result = {
                            "source": "capture-node-timer",
                            "indexed_data_count": len(records),
                        }
                        if data_index_error is not None:
                            result["data_index_error"] = data_index_error
                        self.runtime.complete_run(
                            run.run_id,
                            status="succeeded",
                            external_id=capture_id,
                            result=result,
                        )
                        self.runtime.append_event(
                            "run.succeeded",
                            run_id=run.run_id,
                            activity_id=activity_id,
                            node_id=node_id,
                            payload={
                                "source": "capture-node-timer",
                                "capture_id": capture_id,
                                "indexed_data_count": len(records),
                            },
                        )
                    else:
                        error = self._native_failure_message(event)
                        self.runtime.complete_run(
                            run.run_id,
                            status="failed",
                            external_id=capture_id,
                            error=error,
                        )
                        self.runtime.append_event(
                            "run.failed",
                            severity="error",
                            run_id=run.run_id,
                            activity_id=activity_id,
                            node_id=node_id,
                            payload={"source": "capture-node-timer", "error": error},
                        )
                    runs_added += 1
                if node_id in self._native_sync_failures:
                    self._native_sync_failures.remove(node_id)
                    self.runtime.append_event(
                        "capture.history_sync_recovered", node_id=node_id
                    )
            except Exception as exc:
                failures.append({"node_id": node_id, "error": str(exc)})
                if node_id not in self._native_sync_failures:
                    self._native_sync_failures.add(node_id)
                    self.runtime.append_event(
                        "capture.history_sync_failed",
                        severity="error",
                        node_id=node_id,
                        payload={"error": f"{type(exc).__name__}: {exc}"},
                    )
        return {
            "runs_added": runs_added,
            "data_indexed": data_indexed,
            "failures": failures,
        }

    def _link_native_record(
        self, record, *, revision_id: int | None
    ):
        capture_id = self._capture_id_from_uri(record.uri)
        if revision_id is None or capture_id is None:
            return record, False
        existing = self.runtime.find_run_by_external_id(record.node_id, capture_id)
        if existing is not None:
            return record.model_copy(
                update={"activity_id": existing.activity_id, "run_id": existing.run_id}
            ), False
        scheduled_at = self._capture_time_from_uri(record.uri) or record.captured_at
        activity_id = self._native_activity_id(record.node_id, capture_id)
        run = self.runtime.claim_run(
            revision_id=revision_id,
            activity_id=activity_id,
            node_id=record.node_id,
            scheduled_at=scheduled_at,
        )
        if run is None:
            existing = self.runtime.find_run_by_external_id(record.node_id, capture_id)
            if existing is None:
                return record, False
            run = existing
            created = False
        else:
            run = self.runtime.complete_run(
                run.run_id,
                status="succeeded",
                external_id=capture_id,
                result={"source": "capture-node-storage"},
            )
            created = True
        return record.model_copy(
            update={"activity_id": run.activity_id, "run_id": run.run_id}
        ), created

    @staticmethod
    def _capture_id_from_uri(uri: str) -> str | None:
        name = unquote(urlsplit(uri).path).rsplit("/", 1)[-1]
        match = re.search(r"_([0-9a-fA-F]{32})\.[^.]+$", name)
        return match.group(1).lower() if match else None

    @staticmethod
    def _capture_time_from_uri(uri: str) -> datetime | None:
        name = unquote(urlsplit(uri).path).rsplit("/", 1)[-1]
        match = re.match(r"(\d{8}T\d{6}(?:\.\d{1,6})?Z)_", name)
        if match is None:
            return None
        value = match.group(1)
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
        except ValueError:
            try:
                return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            except ValueError:
                return None

    @staticmethod
    def _event_time(event: dict[str, Any]) -> datetime:
        value = event.get("timestamp")
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.astimezone(UTC)
            except ValueError:
                pass
        return datetime.now(UTC)

    @staticmethod
    def _native_activity_id(node_id: str, capture_id: str) -> str:
        return f"native-{node_id}-{capture_id}"[:100]

    @staticmethod
    def _native_failure_message(event: dict[str, Any]) -> str:
        detail = event.get("failures") or event.get("cause")
        return f"CaptureNode timer capture failed: {detail or event.get('event_type')}"

    def node_health(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for node_id, adapter in self.adapters.items():
            try:
                results.append({"node_id": node_id, "healthy": True, **adapter.health()})
            except Exception as exc:
                results.append({"node_id": node_id, "healthy": False, "error": str(exc)})
        return results

    def preview_frame(self, node_id: str) -> tuple[bytes, str]:
        adapter = self.adapters.get(node_id)
        if adapter is None:
            raise ValueError(f"node has no preview adapter: {node_id}")
        return adapter.preview_frame()

    def open_preview_stream(self, node_id: str) -> PreviewByteStream:
        adapter = self.adapters.get(node_id)
        if adapter is None:
            raise ValueError(f"node has no preview adapter: {node_id}")
        return adapter.open_preview_stream()


class ExecutionWorker:
    def __init__(
        self,
        engine: ExecutionEngine,
        *,
        interval_seconds: float = 1.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.engine = engine
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("execution worker is already running")
        self.engine.recover()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="pi-node-supervisor-executor", daemon=False
        )
        self._thread.start()

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        # Node requests have finite adapter timeouts. Drain an accepted activity
        # instead of abandoning it during process shutdown.
        thread.join()
        self._thread = None

    def _run(self) -> None:
        try:
            self.engine.sync_data()
            self.engine.sync_native_captures()
        except Exception as exc:
            self.engine.runtime.append_event(
                "capture.observer_failed",
                severity="error",
                payload={"error": f"{type(exc).__name__}: {exc}"},
            )
        while not self._stop.is_set():
            try:
                self.engine.tick()
            except Exception as exc:
                self.engine.runtime.append_event(
                    "executor.tick_failed",
                    severity="error",
                    payload={"error": f"{type(exc).__name__}: {exc}"},
                )
            self._stop.wait(self.interval_seconds)
