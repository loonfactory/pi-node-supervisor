"""Composition root for the active v2 Supervisor runtime."""

from __future__ import annotations

import asyncio
import shutil
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from .errors import ApplyError
from .runtime_lifecycle import RuntimeLifecycle
from .capture_v2 import CaptureNodeBundleClient
from .executor_v2 import CameraWorker, ExecutionEngineV2, SensorWorker
from .power_v2 import PowerManagerCli, PowerPlanV2, RuntimePowerCoordinator
from .process_manager_v2 import CaptureNodeProcessManagerV2
from .runtime_v2 import ActiveWork, RunRecordV2, RuntimeStoreV2
from .sensor_v2 import CwtSensorProviderV2
from .service_v2 import SupervisorServiceV2
from .storage_v2 import StoragePipeline


_T = TypeVar("_T")


class RuntimeControllerV2:
    def __init__(
        self,
        service: SupervisorServiceV2,
        *,
        capture_node_executable: Path | None,
        staging_directory: Path,
        local_storage_root: Path | None = None,
        power_manager_executable: Path | None = None,
        power_manager_state_path: Path | None = None,
        power_manager_hold_path: Path | None = None,
    ) -> None:
        self.service = service
        self.capture_node_executable = capture_node_executable
        self.staging_directory = staging_directory
        self.local_storage_root = (
            local_storage_root or staging_directory.parent / "storage"
        )
        self.power_manager_executable = power_manager_executable
        self.power_manager_state_path = power_manager_state_path
        self.power_manager_hold_path = power_manager_hold_path
        self.runtime = RuntimeStoreV2(service.store.database_path)
        self.active_work = ActiveWork()
        self._lifecycle = RuntimeLifecycle()
        self.process_manager: CaptureNodeProcessManagerV2 | None = None
        self.engine: ExecutionEngineV2 | None = None
        self.power: RuntimePowerCoordinator | None = None
        self.providers: list[CwtSensorProviderV2] = []

    def start(self) -> None:
        active = self.service.active_resolution()
        if active is None:
            return
        revision_id, resolution = active
        self.runtime.recover_interrupted()
        self._clean_incomplete_staging()
        if self.power_manager_executable and self.power_manager_state_path:
            manager = PowerManagerCli(
                self.power_manager_executable,
                self.service.power_configuration_path,
                self.power_manager_state_path,
                self.power_manager_hold_path,
            )
            self.power = RuntimePowerCoordinator(
                PowerPlanV2(
                    resolution.resolved, local_timezone=self.service.local_timezone
                ),
                self.active_work,
                manager,
                self.service.power_configuration_path,
                timezone_name=self.service.timezone_name,
            )
        if resolution.resolved.cameras:
            if self.capture_node_executable is None:
                raise RuntimeError("active cameras require --capture-node-executable")
            self.process_manager = CaptureNodeProcessManagerV2(
                self.capture_node_executable,
                self.service.generated_capture_directory,
                resolution.capture_node_configs,
                health_check=self._health_check,
            )
            self.process_manager.start()
        endpoints = self.process_manager.endpoints if self.process_manager else {}
        camera_workers = {
            device_id: CameraWorker(
                camera,
                CaptureNodeBundleClient(
                    endpoints[device_id], self.staging_directory / device_id
                ),
                self._storage_pipeline(device_id, camera.storages),
                self.runtime,
                self.active_work,
                local_timezone=self.service.local_timezone,
            )
            for device_id, camera in resolution.resolved.cameras.items()
        }
        bus_locks: dict[str, threading.Lock] = {}
        sensor_workers = {}
        for device_id, sensor in resolution.resolved.sensors.items():
            provider = CwtSensorProviderV2(sensor.driver)
            self.providers.append(provider)
            sensor_workers[device_id] = SensorWorker(
                sensor,
                provider,
                self._storage_pipeline(device_id, sensor.storages),
                self.runtime,
                self.active_work,
                bus_locks.setdefault(sensor.driver.port, threading.Lock()),
                local_timezone=self.service.local_timezone,
            )
        self.engine = ExecutionEngineV2(
            resolution.resolved,
            revision_id,
            self.runtime,
            camera_workers,
            sensor_workers,
            local_timezone=self.service.local_timezone,
        )

    def stop(self) -> None:
        if self.power is not None:
            self.power.suspend()
            self.power = None
        for provider in self.providers:
            try:
                provider.close()
            except Exception:
                pass
        self.providers.clear()
        if self.process_manager is not None:
            self.process_manager.stop()
            self.process_manager = None
        self.engine = None

    def _reload(self) -> None:
        self.stop()
        self.active_work = ActiveWork()
        self.start()

    async def reload(self) -> None:
        await self._lifecycle.run(
            lambda: asyncio.to_thread(self._reload), exclusive=True
        )

    async def shutdown(self) -> None:
        await self._lifecycle.run(
            lambda: asyncio.to_thread(self.stop), exclusive=True
        )

    async def apply_revision(self, document=None, *, restart=True):
        async def apply():
            return await asyncio.to_thread(self._apply_revision, document, restart)

        return await self._lifecycle.run(apply, exclusive=True)

    def _apply_revision(self, document, restart):
        previous = self.service.active_resolution()
        result = (
            self.service.apply_document(document)
            if document is not None else self.service.apply_draft()
        )
        if restart:
            try:
                self._reload()
            except Exception as exc:
                detail = f"runtime reload failed: {type(exc).__name__}: {exc}"
                self.service.store.rollback_activation(
                    previous[0] if previous else None, result["id"],
                    active_configuration_path=self.service.active_configuration_path,
                    generated_capture_directory=self.service.generated_capture_directory,
                    power_configuration_path=self.service.power_configuration_path,
                    error=detail,
                )
                self._reload()
                raise ApplyError(detail) from exc
        return result

    async def tick(self, now: datetime | None = None) -> tuple[RunRecordV2, ...]:
        async def execute():
            if self.engine is None:
                return ()
            result = await self.engine.tick(now)
            if self.power is not None:
                await asyncio.to_thread(self.power.refresh, now or datetime.now(UTC))
            return result

        return await self._lifecycle.run(execute)

    async def capture_all(self) -> tuple[RunRecordV2, ...]:
        async def capture() -> tuple[RunRecordV2, ...]:
            if self.engine is None or not self.engine.camera_workers:
                raise ValueError("no cameras are active")
            tasks = [
                asyncio.create_task(self.engine.capture_now(device_id))
                for device_id in sorted(self.engine.camera_workers)
            ]
            return await self._finish_all(tasks)

        return await self._manual_operation(capture)

    async def capture_one(
        self, device_id: str, parameters: dict[str, Any] | None = None
    ) -> RunRecordV2:
        async def capture() -> RunRecordV2:
            if self.engine is None:
                raise ValueError("runtime is not active")
            return await self.engine.capture_now(device_id, parameters)

        return await self._manual_operation(capture)

    async def collect_all_sensors(self) -> tuple[RunRecordV2, ...]:
        async def collect() -> tuple[RunRecordV2, ...]:
            if self.engine is None or not self.engine.sensor_workers:
                raise ValueError("no sensors are active")
            tasks = [
                asyncio.create_task(self.engine.collect_now(device_id))
                for device_id in sorted(self.engine.sensor_workers)
            ]
            return await self._finish_all(tasks)

        return await self._manual_operation(collect)

    @staticmethod
    async def _finish_all(tasks) -> tuple[RunRecordV2, ...]:
        # Keep the lifecycle reservation until every device has finished, even
        # when one task raises before the others complete.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return tuple(results)

    async def _manual_operation(
        self, operation: Callable[[], Awaitable[_T]]
    ) -> _T:
        async def execute():
            power = self.power
            if power is None:
                return await operation()
            await asyncio.to_thread(power.begin_manual_override)
            try:
                return await operation()
            finally:
                await asyncio.to_thread(power.end_manual_override, datetime.now(UTC))

        return await self._lifecycle.run(execute)

    def node_documents(self) -> list[dict[str, Any]]:
        active = self.service.active_resolution()
        if active is None:
            return []
        _revision_id, resolution = active
        endpoints = self.process_manager.endpoints if self.process_manager else {}
        result = [
            {
                "id": device_id,
                "kind": "camera",
                "endpoint": endpoints.get(device_id),
                "active": self.engine is not None,
            }
            for device_id in resolution.resolved.cameras
        ]
        result.extend(
            {
                "id": device_id,
                "kind": "sensor",
                "driver": sensor.driver.name,
                "port": sensor.driver.port,
                "active": self.engine is not None,
            }
            for device_id, sensor in resolution.resolved.sensors.items()
        )
        return result

    def _health_check(self, spec) -> None:
        CaptureNodeBundleClient(
            spec.endpoint, self.staging_directory / spec.device_id
        ).health()

    def _clean_incomplete_staging(self) -> None:
        """Remove only CaptureNode bundle directories created under staging."""
        root = self.staging_directory.resolve()
        if root == Path(root.anchor):
            raise ValueError("staging directory must not be a filesystem root")
        if not root.exists():
            root.mkdir(parents=True, exist_ok=True, mode=0o750)
            return
        for device_directory in root.iterdir():
            if not device_directory.is_dir() or device_directory.is_symlink():
                continue
            for candidate in device_directory.glob("capture-*"):
                if candidate.is_dir() and not candidate.is_symlink():
                    shutil.rmtree(candidate)

    def _storage_pipeline(self, device_id: str, storages) -> StoragePipeline:
        pipeline = StoragePipeline.from_resolved(
            storages, local_root=self.local_storage_root
        )
        for result in pipeline.cleanup_temporary():
            if not result.succeeded:
                self.runtime.append_event(
                    "storage.temporary_cleanup_failed",
                    severity="warning",
                    device_id=device_id,
                    payload={"target": result.target, "error": result.error},
                )
        return pipeline
