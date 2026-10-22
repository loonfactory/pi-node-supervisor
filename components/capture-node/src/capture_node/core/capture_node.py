"""CaptureNode Core orchestration."""

from __future__ import annotations

import threading
import time
import uuid
from hashlib import sha256
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from capture_node.core.cameras import CameraOptions, CameraProvider, Capture
from capture_node.core.config import (
    Configuration,
    UpdateResult,
)
from capture_node.core.encodings import ImageEncoder
from capture_node.core.encodings.options import ImageFormatsOptions
from capture_node.core.events import EventEmitter, EventType, cause_payload
from capture_node.core.maintenance import (
    MaintenanceScheduler,
    RetentionOptions,
    StorageMaintenance,
)
from capture_node.core.options import (
    IntervalCaptureOptions,
    DailyCaptureOptions,
    Options,
)
from capture_node.core.scheduler import (
    CaptureSchedule,
    CaptureScheduler,
    IntervalCaptureSchedule,
    OnceCaptureSchedule,
)
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


class CaptureInProgress(RuntimeError):
    """Raised when another capture bundle still owns the capture reservation."""


class InvalidCaptureParameters(ValueError):
    """Raised when request-scoped camera parameters cannot be applied safely."""


class CaptureFailed(RuntimeError):
    """Raised when the camera cannot produce a requested still capture."""


class EncodingFailed(RuntimeError):
    """Raised when a captured image cannot be encoded into its configured formats."""


@dataclass(frozen=True, slots=True)
class CaptureArtifact:
    """One encoded artifact in a deterministic capture bundle."""

    name: str
    media_type: str
    data: bytes
    digest: bytes


class CaptureBundle:
    """Owns encoded artifacts and the capture reservation until explicitly closed."""

    def __init__(
        self,
        request_id: str,
        capture_id: str,
        captured_at: datetime,
        artifacts: tuple[CaptureArtifact, ...],
        release: Callable[[], None],
    ) -> None:
        self.request_id = request_id
        self.capture_id = capture_id
        self.captured_at = captured_at
        self.artifacts = artifacts
        self._release = release
        self._closed = False
        self._close_lock = threading.Lock()

    def close(self) -> None:
        """Release the single-capture reservation exactly once."""

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._release()


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
        maintenance_schedulers: list[MaintenanceScheduler] = []
        for storage in configuration.current.storages:
            policy = storage.maintenance
            if policy is None:
                continue
            maintenance = StorageMaintenance(
                storage_provider,
                (
                    RetentionOptions(
                        target=storage.name,
                        maxAgeDays=policy.max_age_days,
                    ),
                ),
            )
            maintenance_schedulers.append(
                MaintenanceScheduler(
                    policy.interval_seconds,
                    lambda maintenance=maintenance: self._run_maintenance(
                        maintenance
                    ),
                )
            )
        self._maintenance_schedulers = tuple(maintenance_schedulers)
        self._encoder = encoder or ImageEncoder()
        self._emitter = emitter or EventEmitter()
        self._pending_condition = threading.Condition()
        self._pending_since: float | None = None
        self._accepting_work = True
        self._active_options = configuration.current
        self._scheduler = CaptureScheduler(
            self._capture_schedule(self._active_options),
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
            self._camera_provider.camera().apply(self.options.camera.params)
            self._active_options = self.options
            self._scheduler.start()
            for scheduler in self._maintenance_schedulers:
                scheduler.start()
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
            for scheduler in self._maintenance_schedulers:
                scheduler.stop()
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

    def capture_bundle(
        self,
        request_id: str,
        parameters: dict[str, Any],
        image_formats: ImageFormatsOptions | None = None,
    ) -> CaptureBundle:
        """Capture and encode one bundle held until the response stream closes."""

        if not self._reserve_collection():
            raise CaptureInProgress("a capture is already in progress")

        capture_id = uuid.uuid4().hex
        options = self.options
        requested_formats = options.images if image_formats is None else image_formats
        try:
            if (
                requested_formats.dng is not None
                and options.camera.type != "picamera2"
            ):
                raise InvalidCaptureParameters(
                    "DNG raw capture requires a picamera2 camera"
                )
            captured = self._capture_with_temporary_parameters(
                parameters,
                options,
                include_raw=requested_formats.dng is not None,
            )
            self._emitter.emit(
                EventType.CAPTURE_SUCCEEDED,
                capture_id=capture_id,
                request_id=request_id,
            )
            captured = replace(
                captured,
                parameters={
                    **captured.parameters,
                    "captureId": capture_id,
                    "requestId": request_id,
                },
            )
            try:
                artifacts = self._capture_artifacts(
                    captured,
                    capture_id,
                    requested_formats,
                )
            except BaseException as exc:
                self._emitter.emit(
                    EventType.TERMINAL_DISCARDED,
                    capture_id=capture_id,
                    failures=[
                        {
                            "target": "capture-encoding",
                            "cause": cause_payload(exc),
                        }
                    ],
                )
                raise EncodingFailed(str(exc) or type(exc).__name__) from exc
            return CaptureBundle(
                request_id,
                capture_id,
                captured.captured_at,
                artifacts,
                self._release_collection,
            )
        except BaseException:
            self._release_collection()
            raise

    def update_options(self, document: dict[str, Any]) -> UpdateResult:
        before = self.options
        result = self._configuration.replace(document, self._apply_runtime_options)
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
            self._active_options = result.options
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
        image_formats = self.options.images
        try:
            try:
                captured = self._camera_provider.camera().capture(
                    include_raw=image_formats.dng is not None
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
                    parameters={**captured.parameters, "captureId": capture_id},
                )
                artifacts: list[DistributionArtifact] = []
                for options in image_formats.processed:
                    encoded = self._encoder.encode(captured, options)
                    artifacts.append(
                        DistributionArtifact(
                            encoded.data,
                            self._capture_name(
                                captured.captured_at,
                                capture_id,
                                encoded.extension,
                            ),
                        )
                    )
                if image_formats.dng is not None:
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

    def _capture_with_temporary_parameters(
        self,
        parameters: dict[str, Any],
        options: Options,
        *,
        include_raw: bool,
    ) -> Capture:
        camera = self._camera_provider.camera()
        configured = dict(options.camera.params)
        requested = {**configured, **parameters}
        if parameters:
            try:
                camera.apply(requested)
            except BaseException as exc:
                try:
                    camera.apply(configured)
                except BaseException as restore_exc:
                    raise InvalidCaptureParameters(
                        f"{exc}; restoring configured parameters failed: {restore_exc}"
                    ) from exc
                raise InvalidCaptureParameters(
                    str(exc) or type(exc).__name__
                ) from exc
        try:
            return camera.capture(include_raw=include_raw)
        except BaseException as exc:
            self._emitter.emit(
                EventType.CAPTURE_FAILED,
                cause=cause_payload(exc),
            )
            raise CaptureFailed(str(exc) or type(exc).__name__) from exc
        finally:
            if parameters:
                try:
                    camera.apply(configured)
                except BaseException as exc:
                    raise CaptureFailed(
                        "captured the image but could not restore configured "
                        f"camera parameters: {exc}"
                    ) from exc

    def _capture_artifacts(
        self,
        captured: Capture,
        capture_id: str,
        image_formats: ImageFormatsOptions,
    ) -> tuple[CaptureArtifact, ...]:
        artifacts: list[CaptureArtifact] = []
        for options in image_formats.processed:
            encoded = self._encoder.encode(captured, options)
            name = self._capture_name(
                captured.captured_at,
                capture_id,
                encoded.extension,
            )
            artifacts.append(
                CaptureArtifact(
                    name,
                    f"image/{'jpeg' if encoded.extension in ('jpg', 'jpeg') else encoded.extension}",
                    encoded.data,
                    sha256(encoded.data).digest(),
                )
            )
        if image_formats.dng is not None:
            if captured.raw is None:
                raise RuntimeError("camera did not return the requested DNG raw capture")
            name = self._capture_name(
                captured.captured_at,
                capture_id,
                captured.raw.extension,
            )
            artifacts.append(
                CaptureArtifact(
                    name,
                    captured.raw.media_type,
                    captured.raw.data,
                    sha256(captured.raw.data).digest(),
                )
            )
        return tuple(artifacts)

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

    def _run_maintenance(self, maintenance: StorageMaintenance) -> None:
        try:
            result = maintenance.run()
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

    def _apply_runtime_options(self, candidate: Options) -> None:
        previous = self._active_options
        if candidate.camera.params != previous.camera.params:
            camera = self._camera_provider.camera()
            try:
                camera.apply(candidate.camera.params)
            except BaseException:
                camera.apply(previous.camera.params)
                raise
        self._scheduler.update_schedule(self._capture_schedule(candidate))
        self._active_options = candidate

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
            "cameraType": options.camera.type,
            "captures": [
                capture.model_dump(mode="json") for capture in options.captures
            ],
            "imageFormats": [image.format for image in options.images.enabled],
            "storageTargets": [storage.name for storage in options.storages],
            "retentionTargets": [
                storage.name
                for storage in options.storages
                if storage.maintenance is not None
            ],
        }

    @staticmethod
    def _changed_summary(before: Options, after: Options) -> dict[str, Any]:
        changed: dict[str, Any] = {}
        before_camera = before.camera.model_dump(
            mode="json",
            exclude={"calibration", "params"},
        )
        after_camera = after.camera.model_dump(
            mode="json",
            exclude={"calibration", "params"},
        )
        if before_camera != after_camera:
            changed["camera"] = after.camera.model_dump(mode="json")
        elif before.camera.calibration != after.camera.calibration:
            changed["camera.calibration"] = after.camera.calibration
        if before.captures != after.captures:
            changed["captures"] = [
                capture.model_dump(mode="json") for capture in after.captures
            ]
        if before.camera.params != after.camera.params:
            changed["camera.params"] = after.camera.params
        if before.images != after.images:
            changed["images"] = after.images.model_dump(mode="json")
        return changed

    @staticmethod
    def _capture_schedule(options: Options) -> CaptureSchedule:
        rules = []
        for capture in options.captures:
            if isinstance(capture, DailyCaptureOptions):
                rules.append(OnceCaptureSchedule(capture.time))
            elif isinstance(capture, IntervalCaptureOptions):
                rules.append(
                    IntervalCaptureSchedule(
                        capture.interval,
                        capture.startTime,
                        capture.endTime,
                    )
                )
        return CaptureSchedule(tuple(rules))
