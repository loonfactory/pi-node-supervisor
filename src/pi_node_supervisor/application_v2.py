"""Composition root for the active v2 Supervisor runtime."""

from __future__ import annotations

import asyncio
import shutil
import threading
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from .capture_v2 import CaptureNodeBundleClient
from .executor_v2 import CameraWorker, ExecutionEngineV2, SensorWorker
from .power_v2 import PowerManagerCli, PowerPlanV2, RuntimePowerCoordinator
from .process_manager_v2 import CaptureNodeProcessManagerV2
from .runtime_v2 import ActiveWork, RunRecordV2, RuntimeStoreV2
from .sensor_v2 import CwtSensorProviderV2
from .service_v2 import SupervisorServiceV2
from .storage_v2 import StoragePipeline


class RuntimeControllerV2:
    def __init__(
        self,
        service: SupervisorServiceV2,
        *,
        capture_node_executable: Path | None,
        staging_directory: Path,
        power_manager_executable: Path | None = None,
        power_manager_state_path: Path | None = None,
    ) -> None:
        self.service = service
        self.capture_node_executable = capture_node_executable
        self.staging_directory = staging_directory
        self.power_manager_executable = power_manager_executable
        self.power_manager_state_path = power_manager_state_path
        self.runtime = RuntimeStoreV2(service.store.database_path)
        self.active_work = ActiveWork()
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
        self.power = None

    def reload(self) -> None:
        self.stop()
        self.active_work = ActiveWork()
        self.start()

    async def tick(self, now: datetime | None = None) -> tuple[RunRecordV2, ...]:
        if self.engine is None:
            return ()
        result = await self.engine.tick(now)
        if self.power is not None:
            await asyncio.to_thread(self.power.refresh, now or datetime.now(UTC))
        return result

    async def capture_all(self) -> tuple[RunRecordV2, ...]:
        if self.engine is None or not self.engine.camera_workers:
            raise ValueError("no cameras are active")
        tasks = [
            asyncio.create_task(self.engine.capture_now(device_id))
            for device_id in sorted(self.engine.camera_workers)
        ]
        return tuple(await asyncio.gather(*tasks))

    async def capture_one(
        self, device_id: str, parameters: dict[str, Any] | None = None
    ) -> RunRecordV2:
        if self.engine is None:
            raise ValueError("runtime is not active")
        return await self.engine.capture_now(device_id, parameters)

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
        pipeline = StoragePipeline.from_resolved(storages)
        for result in pipeline.cleanup_temporary():
            if not result.succeeded:
                self.runtime.append_event(
                    "storage.temporary_cleanup_failed",
                    severity="warning",
                    device_id=device_id,
                    payload={"target": result.target, "error": result.error},
                )
        return pipeline
