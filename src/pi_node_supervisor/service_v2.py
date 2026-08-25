"""V2 use-case boundary for CLI and API."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from .config_v2 import Resolution, resolve_configuration
from .power_v2 import PowerPlanV2
from .revision_v2 import RevisionStoreV2


class SupervisorServiceV2:
    def __init__(
        self,
        store: RevisionStoreV2,
        *,
        active_configuration_path: Path,
        generated_capture_directory: Path,
        power_configuration_path: Path,
        local_timezone: tzinfo,
        timezone_name: str,
    ) -> None:
        self.store = store
        self.active_configuration_path = active_configuration_path
        self.generated_capture_directory = generated_capture_directory
        self.power_configuration_path = power_configuration_path
        self.local_timezone = local_timezone
        self.timezone_name = timezone_name

    def preview(self, document: dict[str, Any]) -> dict[str, Any]:
        resolution = resolve_configuration(document)
        power = PowerPlanV2(
            resolution.resolved, local_timezone=self.local_timezone
        ).manager_document(datetime.now(UTC), timezone_name=self.timezone_name)
        return {
            "sourceHash": resolution.resolved.source_hash,
            "resolved": resolution.resolved.model_dump(mode="json"),
            "warnings": list(resolution.resolved.warnings),
            "captureNodes": resolution.capture_node_configs,
            "piPowerManager": power,
        }

    def save_draft(self, document: dict[str, Any]) -> dict[str, Any]:
        resolution = self.store.save_draft(document)
        return {
            "saved": True,
            "sourceHash": resolution.resolved.source_hash,
            "warnings": list(resolution.resolved.warnings),
        }

    def apply_document(self, document: dict[str, Any]) -> dict[str, Any]:
        resolution = resolve_configuration(document)
        power = PowerPlanV2(
            resolution.resolved, local_timezone=self.local_timezone
        ).manager_document(datetime.now(UTC), timezone_name=self.timezone_name)
        self.store.save_draft(document)
        return self.store.activate(
            resolution,
            power,
            active_configuration_path=self.active_configuration_path,
            generated_capture_directory=self.generated_capture_directory,
            power_configuration_path=self.power_configuration_path,
        )

    def apply_draft(self) -> dict[str, Any]:
        return self.apply_document(self.store.load_draft())

    def draft(self) -> dict[str, Any] | None:
        try:
            return self.store.load_draft()
        except Exception:
            return None

    def active(self) -> dict[str, Any] | None:
        active = self.store.active()
        if active is None:
            return None
        _revision_id, resolution = active
        return resolution.source.model_dump(mode="json", by_alias=True)

    def active_resolution(self) -> tuple[int, Resolution] | None:
        return self.store.active()
