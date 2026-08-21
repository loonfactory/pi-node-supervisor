"""CaptureNode Core orchestration."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from capture_node.core.cameras import CameraOptions, CameraProvider
from capture_node.core.config import (
    Configuration,
    UpdateResult,
)
from capture_node.core.encodings import ImageEncoder
from capture_node.core.events import EventEmitter, EventType, cause_payload
from capture_node.core.maintenance import MaintenanceScheduler, StorageMaintenance
from capture_node.core.options import CaptureOptions, Options
from capture_node.core.scheduler import CaptureSchedule, CaptureScheduler
from capture_node.core.storages import (
    DistributionResult,
    DistributionArtifact,
    Distributor,
    StorageProvider,
    TerminalState,
)
from capture_node.version import VERSION


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """Synchronous outcome of capture, encoding, and storage handoff."""

    accepted: bool
    capture_id: str | None = None
    captured_at: datetime | None = None
    error: BaseException | None = None

    def __post_init__(self) -> None:
        if not self.accepted and (
            self.capture_id is not None
            or self.captured_at is not None
            or self.error is not None
        ):
            raise ValueError("a rejected collection cannot have an outcome")
        if self.accepted and self.capture_id is None:
            raise ValueError("an accepted collection requires a capture id")


class CaptureNode:
    """Owns scheduling and one-at-a-time still collection."""

    def __init__(
        self,
        camera_provider: CameraProvider,
        configuration: Configuration,
        storage_provider: StorageProvider,
        *,
        encoder: ImageEncoder | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        self._camera_provider = camera_provider
        self._configuration = configuration
        self._distributor = Distributor(storage_provider)
        self._maintenance = StorageMaintenance(
            storage_provider,
            configuration.current.maintenance.retention,
        )
        self._maintenance_scheduler = (
            MaintenanceScheduler(
                configuration.current.maintenance.interval_seconds,
                self._run_maintenance,
            )
            if configuration.current.maintenance.retention
            else None
        )
        self._encoder = encoder or ImageEncoder()
        self._emitter = emitter or EventEmitter()
        self._pending_condition = threading.Condition()
        self._pending_since: float | None = None
        self._accepting_work = True
        self._active_capture_options = configuration.current.capture
        self._scheduler = CaptureScheduler(
            self._capture_schedule(self._active_capture_options),
            self._on_tick,
        )
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="capture-node-collection",
        )
        self._started = False
        self._stopped = False
        self._lifecycle_lock = threading.Lock()

    @property
    def options(self) -> Options:
        return self._configuration.current

    @property
    def is_pending(self) -> bool:
        with self._pending_condition:
            return self._pending_since is not None

    @property
    def is_accepting_work(self) -> bool:
        with self._pending_condition:
            return self._accepting_work

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                raise RuntimeError("capture node is already started")
            if self._stopped:
                raise RuntimeError("capture node cannot restart after it has stopped")
            self._camera_provider.camera().apply(self.options.capture.params)
            self._active_capture_options = self.options.capture
            self._scheduler.start()
            if self._maintenance_scheduler is not None:
                self._maintenance_scheduler.start()
            self._started = True
        self._emitter.emit(
            EventType.STARTED,
            version=VERSION,
            config_summary=self._config_summary(self.options),
        )
        if not self._distributor.targets:
            self._emitter.emit(EventType.NO_STORAGE_TARGETS)

    def stop(self) -> None:
        """Reject new collections, drain accepted work, and stop permanently."""

        with self._lifecycle_lock:
            if self._stopped:
                return
            was_started = self._started
            with self._pending_condition:
                self._accepting_work = False
            self._scheduler.stop()
            if self._maintenance_scheduler is not None:
                self._maintenance_scheduler.stop()
            self._executor.shutdown(wait=True)
            with self._pending_condition:
                while self._pending_since is not None:
                    self._pending_condition.wait()
            self._distributor.close()
            self._started = False
            self._stopped = True
        if was_started:
            self._emitter.emit(EventType.STOPPED, version=VERSION)

    def collect_once(self) -> CollectionResult:
        if not self._reserve_collection():
            return CollectionResult(False)
        return self._run_collection()

    def update_options(self, document: dict[str, Any]) -> UpdateResult:
        before = self.options
        result = self._configuration.replace(document, self._apply_capture_options)
        if result.succeeded:
            self._emitter.emit(
                EventType.CONFIG_CHANGED,
                changed_summary=self._changed_summary(before, result.options),
            )
        return result

    def update_camera_options(
        self,
        document: dict[str, Any],
        apply: Callable[[CameraOptions], None],
    ) -> UpdateResult:
        before = self.options
        result = self._configuration.replace_camera(document, apply)
        if result.succeeded:
            self._emitter.emit(
                EventType.CONFIG_CHANGED,
                changed_summary=self._changed_summary(before, result.options),
            )
        return result

    def _on_tick(self) -> None:
        if not self._reserve_collection():
            with self._pending_condition:
                pending_since = self._pending_since
            if pending_since is None:
                return
            elapsed = time.monotonic() - pending_since
            self._emitter.emit(
                EventType.CAPTURE_SKIPPED,
                pending_elapsed=elapsed,
            )
            return
        self._executor.submit(self._run_collection)

    def _reserve_collection(self) -> bool:
        with self._pending_condition:
            if not self._accepting_work or self._pending_since is not None:
                return False
            self._pending_since = time.monotonic()
            return True

    def _run_collection(self) -> CollectionResult:
        capture_id = uuid.uuid4().hex
        captured = None
        handed_off = False
        try:
            try:
                captured = self._camera_provider.camera().capture(
                    include_raw=self.options.image.raw.enabled
                )
            except BaseException as exc:
                self._emitter.emit(
                    EventType.CAPTURE_FAILED,
                    capture_id=capture_id,
                    cause=cause_payload(exc),
                )
                return CollectionResult(True, capture_id, error=exc)

            self._emitter.emit(
                EventType.CAPTURE_SUCCEEDED,
                capture_id=capture_id,
            )
            try:
                captured = replace(
                    captured,
                    parameters={**captured.parameters, "capture_id": capture_id},
                )
                encoded = self._encoder.encode(captured, self.options.image)
                name = self._capture_name(captured.captured_at, capture_id, encoded.extension)
                artifacts = [DistributionArtifact(encoded.data, name)]
                if self.options.image.raw.enabled:
                    if captured.raw is None:
                        raise RuntimeError(
                            "camera did not return the requested DNG raw capture"
                        )
                    artifacts.append(
                        DistributionArtifact(
                            captured.raw.data,
                            self._capture_name(
                                captured.captured_at,
                                capture_id,
                                captured.raw.extension,
                            ),
                        )
                    )
                if not self._distributor.targets:
                    outcome = self._distributor.distribute_many(artifacts)
                    self._emit_distribution(capture_id, outcome)
                    return CollectionResult(
                        True,
                        capture_id,
                        captured_at=captured.captured_at,
                    )
                future = self._distributor.submit_many(artifacts)
                handed_off = True
                future.add_done_callback(
                    lambda completed: self._complete_distribution(
                        capture_id,
                        completed,
                    )
                )
            except BaseException as exc:
                self._emitter.emit(
                    EventType.TERMINAL_DISCARDED,
                    capture_id=capture_id,
                    failures=[
                        {
                            "target": "capture-pipeline",
                            "cause": cause_payload(exc),
                        }
                    ],
                )
                return CollectionResult(
                    True,
                    capture_id,
                    captured_at=captured.captured_at,
                    error=exc,
                )
            return CollectionResult(
                True,
                capture_id,
                captured_at=captured.captured_at,
            )
        finally:
            captured = None
            if not handed_off:
                self._release_collection()

    def _complete_distribution(
        self,
        capture_id: str,
        future: Future[DistributionResult],
    ) -> None:
        try:
            self._emit_distribution(capture_id, future.result())
        except BaseException as exc:
            self._emitter.emit(
                EventType.TERMINAL_DISCARDED,
                capture_id=capture_id,
                failures=[
                    {
                        "target": "capture-pipeline",
                        "cause": cause_payload(exc),
                    }
                ],
            )
        finally:
            self._release_collection()

    def _emit_distribution(
        self,
        capture_id: str,
        outcome: DistributionResult,
    ) -> None:
        if outcome.state is TerminalState.COMPLETED:
            self._emitter.emit(
                EventType.TERMINAL_COMPLETED,
                capture_id=capture_id,
                targets=list(outcome.targets),
                artifacts=list(outcome.artifacts),
            )
            return
        self._emitter.emit(
            EventType.TERMINAL_DISCARDED,
            capture_id=capture_id,
            failures=[
                {
                    "target": failure.target,
                    "name": failure.name,
                    "cause": cause_payload(failure.cause),
                }
                for failure in outcome.failures
            ],
        )

    def _release_collection(self) -> None:
        with self._pending_condition:
            self._pending_since = None
            self._pending_condition.notify_all()

    def _run_maintenance(self) -> None:
        try:
            result = self._maintenance.run()
        except BaseException as exc:
            self._emitter.emit(
                EventType.RETENTION_FAILED,
                failures=[{"target": None, "name": None, "cause": cause_payload(exc)}],
            )
            return
        if result.deleted:
            self._emitter.emit(
                EventType.RETENTION_COMPLETED,
                deleted=[
                    {"target": target, "name": name}
                    for target, name in result.deleted
                ],
            )
        if result.failures:
            self._emitter.emit(
                EventType.RETENTION_FAILED,
                failures=[
                    {
                        "target": failure.target,
                        "name": failure.name,
                        "cause": cause_payload(failure.cause),
                    }
                    for failure in result.failures
                ],
            )

    def _apply_capture_options(self, candidate: CaptureOptions) -> None:
        previous = self._active_capture_options
        if candidate.params != previous.params:
            camera = self._camera_provider.camera()
            try:
                camera.apply(candidate.params)
            except BaseException:
                camera.apply(previous.params)
                raise
        self._scheduler.update_schedule(self._capture_schedule(candidate))
        self._active_capture_options = candidate

    @staticmethod
    def _capture_name(
        captured_at: datetime,
        capture_id: str,
        extension: str,
    ) -> str:
        timestamp = captured_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"{timestamp}_{capture_id}.{extension}"

    @staticmethod
    def _config_summary(options: Options) -> dict[str, Any]:
        return {
            "camera_type": options.camera.type,
            "capture_mode": options.capture.mode,
            "capture_interval_seconds": options.capture.interval_seconds,
            "capture_daily_times": [
                value.isoformat() for value in options.capture.daily_times
            ],
            "capture_timezone": options.capture.timezone,
            "image_format": options.image.format,
            "raw_format": options.image.raw.format if options.image.raw.enabled else None,
            "storage_targets": [storage.name for storage in options.storages],
            "retention_targets": [
                rule.target for rule in options.maintenance.retention
            ],
        }

    @staticmethod
    def _changed_summary(before: Options, after: Options) -> dict[str, Any]:
        changed: dict[str, Any] = {}
        if before.camera != after.camera:
            changed["camera"] = after.camera.model_dump(mode="json")
        if before.capture.mode != after.capture.mode:
            changed["capture.mode"] = after.capture.mode
        if before.capture.interval_seconds != after.capture.interval_seconds:
            changed["capture.interval_seconds"] = after.capture.interval_seconds
        if before.capture.daily_times != after.capture.daily_times:
            changed["capture.daily_times"] = [
                value.isoformat() for value in after.capture.daily_times
            ]
        if before.capture.timezone != after.capture.timezone:
            changed["capture.timezone"] = after.capture.timezone
        if before.capture.params != after.capture.params:
            changed["capture.params"] = after.capture.params
        if before.image != after.image:
            changed["image"] = after.image.model_dump(mode="json")
        if before.calibration != after.calibration:
            changed["calibration"] = after.calibration
        return changed

    @staticmethod
    def _capture_schedule(options: CaptureOptions) -> CaptureSchedule:
        return CaptureSchedule(
            mode=options.mode,
            interval_seconds=options.interval_seconds,
            daily_times=options.daily_times,
            timezone=options.timezone,
        )
