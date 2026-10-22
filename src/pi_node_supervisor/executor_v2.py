"""Device-isolated v2 workers for capture, collection, and Storage completion."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import PurePosixPath
from typing import Any, Protocol

from .capture_v2 import CaptureNodeBundleClient
from .config_v2 import ResolvedCamera, ResolvedConfiguration, ResolvedSensor
from .runtime_v2 import ActiveWork, RunRecordV2, RuntimeStoreV2
from .schedule_v2 import Occurrence, ScheduleEngine
from .sensor_models import SensorReading
from .storage_v2 import SaveResult, StoragePipeline


class SensorProvider(Protocol):
    def read(self) -> SensorReading: ...
    def close(self) -> None: ...


class CameraWorker:
    def __init__(
        self,
        configuration: ResolvedCamera,
        client: CaptureNodeBundleClient,
        storage: StoragePipeline,
        runtime: RuntimeStoreV2,
        active_work: ActiveWork,
        *,
        local_timezone: tzinfo,
    ) -> None:
        self.configuration = configuration
        self.client = client
        self.storage = storage
        self.runtime = runtime
        self.active_work = active_work
        self.local_timezone = local_timezone
        self._lock = threading.Lock()

    def execute(
        self, run: RunRecordV2, *, parameters: dict[str, Any] | None = None
    ) -> RunRecordV2:
        try:
            self.active_work.acquire()
        except Exception as exc:
            return self._fail(run, exc)
        try:
            with self._lock:
                return self._execute_locked(run, parameters or {})
        finally:
            self.active_work.release()

    def _execute_locked(
        self, run: RunRecordV2, parameters: dict[str, Any]
    ) -> RunRecordV2:
        try:
            self.client.health()
            with self.client.capture(run.run_id, parameters=parameters) as bundle:
                succeeded = 0
                attempted = 0
                artifacts = []
                local_date = bundle.captured_at.astimezone(self.local_timezone)
                for staged in bundle.artifacts:
                    artifact = self.runtime.add_artifact(
                        run_id=run.run_id,
                        name=staged.name,
                        media_type=staged.media_type,
                        size=staged.size,
                        sha256=staged.sha256,
                        captured_at=bundle.captured_at,
                    )
                    relative = PurePosixPath(
                        f"{local_date.year:04d}",
                        f"{local_date.month:02d}",
                        f"{local_date.day:02d}",
                        staged.name,
                    )
                    results = self.storage.save_file(staged.path, relative)
                    self._record_results(run.run_id, artifact.artifact_id, results)
                    succeeded += sum(item.succeeded for item in results)
                    attempted += len(results)
                    artifacts.append(
                        {
                            "artifactId": artifact.artifact_id,
                            "name": staged.name,
                            "size": staged.size,
                            "sha256": staged.sha256,
                        }
                    )
                result = {
                    "captureId": bundle.capture_id,
                    "capturedAt": bundle.captured_at.isoformat(),
                    "artifacts": artifacts,
                    "storageSucceeded": succeeded,
                    "storageAttempted": attempted,
                }
                completed = self.runtime.complete_run(
                    run.run_id,
                    status="succeeded",
                    capture_id=bundle.capture_id,
                    result=result,
                )
                self.runtime.append_event(
                    "run.succeeded",
                    run_id=run.run_id,
                    device_id=run.device_id,
                    payload=result,
                )
                return completed
        except Exception as exc:
            return self._fail(run, exc)

    def _fail(self, run: RunRecordV2, exc: Exception) -> RunRecordV2:
        error = f"{type(exc).__name__}: {exc}"
        failed = self.runtime.complete_run(run.run_id, status="failed", error=error)
        self.runtime.append_event(
            "run.failed",
            severity="error",
            run_id=run.run_id,
            device_id=run.device_id,
            payload={"error": error},
        )
        return failed

    def _record_results(
        self, run_id: str, artifact_id: str | None, results: tuple[SaveResult, ...]
    ) -> None:
        for result in results:
            self.runtime.add_storage_result(
                run_id=run_id,
                artifact_id=artifact_id,
                target=result.target,
                path=result.path,
                succeeded=result.succeeded,
                size=result.size,
                error=result.error,
            )
            self._record_failure_event(run_id, result)

    def _record_failure_event(self, run_id: str, result: SaveResult) -> None:
        if result.succeeded:
            return
        self.runtime.append_event(
            "storage.failed",
            severity="error",
            run_id=run_id,
            device_id=self.configuration.device_id,
            payload={
                "target": result.target,
                "path": result.path,
                "error": result.error,
            },
        )


class SensorWorker:
    HEADER = ("timestamp", "temperatureC", "humidityPercent")

    def __init__(
        self,
        configuration: ResolvedSensor,
        provider: SensorProvider,
        storage: StoragePipeline,
        runtime: RuntimeStoreV2,
        active_work: ActiveWork,
        bus_lock: threading.Lock,
        *,
        local_timezone: tzinfo,
    ) -> None:
        self.configuration = configuration
        self.provider = provider
        self.storage = storage
        self.runtime = runtime
        self.active_work = active_work
        self.bus_lock = bus_lock
        self.execution_lock = threading.Lock()
        self.local_timezone = local_timezone

    async def execute(self, run: RunRecordV2) -> RunRecordV2:
        try:
            self.active_work.acquire()
        except Exception as exc:
            return self._fail(run, exc)
        try:
            return await asyncio.to_thread(self._execute, run)
        finally:
            self.active_work.release()

    def _execute(self, run: RunRecordV2) -> RunRecordV2:
        with self.execution_lock:
            return self._execute_locked(run)

    def _execute_locked(self, run: RunRecordV2) -> RunRecordV2:
        try:
            with self.bus_lock:
                reading = self.provider.read()
            local = reading.observed_at.astimezone(self.local_timezone)
            relative = PurePosixPath(
                f"{local.year:04d}",
                f"{local.month:02d}",
                f"{local.date().isoformat()}.csv",
            )
            results = self.storage.append_sensor_row(
                relative,
                header=self.HEADER,
                row=(
                    local.isoformat(),
                    format(reading.temperature_c, ".15g"),
                    format(reading.humidity_percent, ".15g"),
                ),
            )
            for result in results:
                self.runtime.add_storage_result(
                    run_id=run.run_id,
                    artifact_id=None,
                    target=result.target,
                    path=result.path,
                    succeeded=result.succeeded,
                    size=result.size,
                    error=result.error,
                )
                if not result.succeeded:
                    self.runtime.append_event(
                        "storage.failed",
                        severity="error",
                        run_id=run.run_id,
                        device_id=run.device_id,
                        payload={
                            "target": result.target,
                            "path": result.path,
                            "error": result.error,
                        },
                    )
            result_document = {
                "observedAt": reading.observed_at.isoformat(),
                "temperatureC": reading.temperature_c,
                "humidityPercent": reading.humidity_percent,
                "storageSucceeded": sum(item.succeeded for item in results),
                "storageAttempted": len(results),
            }
            completed = self.runtime.complete_run(
                run.run_id, status="succeeded", result=result_document
            )
            self.runtime.append_event(
                "run.succeeded",
                run_id=run.run_id,
                device_id=run.device_id,
                payload=result_document,
            )
            return completed
        except Exception as exc:
            return self._fail(run, exc)

    def _fail(self, run: RunRecordV2, exc: Exception) -> RunRecordV2:
        error = f"{type(exc).__name__}: {exc}"
        failed = self.runtime.complete_run(run.run_id, status="failed", error=error)
        self.runtime.append_event(
            "run.failed",
            severity="error",
            run_id=run.run_id,
            device_id=run.device_id,
            payload={"error": error},
        )
        return failed


class ExecutionEngineV2:
    def __init__(
        self,
        configuration: ResolvedConfiguration,
        revision_id: int,
        runtime: RuntimeStoreV2,
        camera_workers: dict[str, CameraWorker],
        sensor_workers: dict[str, SensorWorker],
        *,
        local_timezone: tzinfo | None = None,
    ) -> None:
        self.configuration = configuration
        self.revision_id = revision_id
        self.runtime = runtime
        self.camera_workers = camera_workers
        self.sensor_workers = sensor_workers
        self.schedule = ScheduleEngine(
            configuration, local_timezone=local_timezone
        )
        self._tick_lock = asyncio.Lock()

    async def tick(self, now: datetime | None = None) -> tuple[RunRecordV2, ...]:
        evaluation_time = (now or datetime.now(UTC)).astimezone(UTC)
        tolerance = timedelta(seconds=self.configuration.clock_tolerance_seconds)
        async with self._tick_lock:
            occurrences = self.schedule.occurrences(
                evaluation_time - tolerance, evaluation_time
            )
            tasks: list[asyncio.Task[RunRecordV2]] = []
            for occurrence in occurrences:
                run = self.runtime.claim_run(
                    revision_id=self.revision_id,
                    device_id=occurrence.device_id,
                    device_kind=occurrence.device_kind,
                    scheduled_at=occurrence.scheduled_at,
                )
                if run is None:
                    continue
                self.runtime.append_event(
                    "run.started",
                    run_id=run.run_id,
                    device_id=run.device_id,
                    payload={"scheduledAt": occurrence.scheduled_at.isoformat()},
                )
                tasks.append(self._task_for(occurrence, run))
            if not tasks:
                return ()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            return tuple(results)

    async def capture_now(
        self, device_id: str, parameters: dict[str, Any] | None = None
    ) -> RunRecordV2:
        worker = self.camera_workers.get(device_id)
        if worker is None:
            raise ValueError(f"camera does not exist: {device_id}")
        now = datetime.now(UTC)
        run = self.runtime.claim_run(
            revision_id=self.revision_id,
            device_id=device_id,
            device_kind="camera",
            scheduled_at=now,
            trigger="manual",
        )
        if run is None:
            raise RuntimeError("manual capture run could not be claimed")
        self.runtime.append_event(
            "run.started",
            run_id=run.run_id,
            device_id=run.device_id,
            payload={"trigger": "manual"},
        )
        return await asyncio.to_thread(worker.execute, run, parameters=parameters)

    async def collect_now(self, device_id: str) -> RunRecordV2:
        worker = self.sensor_workers.get(device_id)
        if worker is None:
            raise ValueError(f"sensor does not exist: {device_id}")
        now = datetime.now(UTC)
        run = self.runtime.claim_run(
            revision_id=self.revision_id,
            device_id=device_id,
            device_kind="sensor",
            scheduled_at=now,
            trigger="manual",
        )
        if run is None:
            raise RuntimeError("manual sensor run could not be claimed")
        self.runtime.append_event(
            "run.started",
            run_id=run.run_id,
            device_id=run.device_id,
            payload={"trigger": "manual"},
        )
        return await worker.execute(run)

    def _task_for(
        self, occurrence: Occurrence, run: RunRecordV2
    ) -> asyncio.Task[RunRecordV2]:
        if occurrence.device_kind == "camera":
            worker = self.camera_workers.get(occurrence.device_id)
            if worker is None:
                return asyncio.create_task(
                    asyncio.to_thread(self._missing_worker, run)
                )
            return asyncio.create_task(asyncio.to_thread(worker.execute, run))
        worker = self.sensor_workers.get(occurrence.device_id)
        if worker is None:
            return asyncio.create_task(asyncio.to_thread(self._missing_worker, run))
        return asyncio.create_task(worker.execute(run))

    def _missing_worker(self, run: RunRecordV2) -> RunRecordV2:
        error = f"no worker is configured for {run.device_id}"
        return self.runtime.complete_run(run.run_id, status="failed", error=error)
