"""Use-case boundary shared by CLI and local API."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import PersistenceError
from .models import DerivedPlan, PowerManagerConfiguration, SupervisorConfiguration
from .planner import Planner
from .registry import NodeRegistry
from .storage import RevisionStore


class SupervisorService:
    def __init__(
        self,
        registry: NodeRegistry,
        store: RevisionStore,
        *,
        active_configuration_path: Path,
        power_manager_configuration_path: Path,
    ) -> None:
        self.registry = registry
        self.store = store
        self.planner = Planner(registry)
        self.active_configuration_path = active_configuration_path
        self.power_manager_configuration_path = power_manager_configuration_path
        self._power_refresh_lock = threading.Lock()
        self._last_power_refresh: datetime | None = None

    @staticmethod
    def parse_configuration(document: dict[str, Any]) -> SupervisorConfiguration:
        return SupervisorConfiguration.model_validate(document)

    def preview(self, document: dict[str, Any]) -> DerivedPlan:
        return self.planner.plan(self.parse_configuration(document))

    def preview_with_power(self, document: dict[str, Any]) -> dict[str, Any]:
        configuration = self.parse_configuration(document)
        plan = self.planner.plan(configuration)
        power = self.planner.power_manager_configuration(configuration, plan)
        return {
            "plan": plan.model_dump(mode="json"),
            "pi_power_manager": power.model_dump(mode="json", by_alias=True),
        }

    def save_draft(self, document: dict[str, Any]) -> DerivedPlan:
        configuration = self.parse_configuration(document)
        plan = self.planner.plan(configuration)
        self.store.save_draft(configuration)
        return plan

    def apply_document(self, document: dict[str, Any]) -> dict[str, Any]:
        configuration = self.parse_configuration(document)
        plan = self.planner.plan(configuration)
        power = self.planner.power_manager_configuration(configuration, plan)
        self.store.save_draft(configuration)
        return self.store.activate(
            configuration,
            plan,
            power,
            active_configuration_path=self.active_configuration_path,
            power_manager_configuration_path=self.power_manager_configuration_path,
        )

    def apply_draft(self) -> dict[str, Any]:
        configuration = self.store.load_draft()
        return self.apply_document(configuration.model_dump(mode="json", by_alias=True))

    def draft(self) -> dict[str, Any] | None:
        try:
            return self.store.load_draft().model_dump(mode="json", by_alias=True)
        except PersistenceError:
            return None

    def active(self) -> dict[str, Any] | None:
        if not self.active_configuration_path.is_file():
            return None
        try:
            value = json.loads(self.active_configuration_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"cannot read active configuration: {exc}") from exc
        return self.parse_configuration(value).model_dump(mode="json", by_alias=True)

    def power_configuration(self) -> PowerManagerConfiguration | None:
        if not self.power_manager_configuration_path.is_file():
            return None
        try:
            value = json.loads(
                self.power_manager_configuration_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"cannot read pi-power-manager configuration: {exc}") from exc
        return PowerManagerConfiguration.model_validate(value)

    def refresh_recurring_power(self, now: datetime) -> None:
        """Keep finite recurring execution and RTC plans ahead of the clock."""

        evaluation_time = now.astimezone(UTC)
        with self._power_refresh_lock:
            if (
                self._last_power_refresh is not None
                and evaluation_time - self._last_power_refresh < timedelta(hours=1)
            ):
                return
            document = self.active()
            if document is None:
                return
            configuration = self.parse_configuration(document)
            if not (
                configuration.capture_schedules
                or configuration.sensor_schedules
                or configuration.power_keep_schedules
            ):
                self._last_power_refresh = evaluation_time
                return
            plan = self.planner.plan(configuration, now=evaluation_time)
            power = self.planner.power_manager_configuration(configuration, plan)
            self.store.refresh_active_plan(
                plan,
                power,
                power_manager_configuration_path=self.power_manager_configuration_path,
            )
            self._last_power_refresh = evaluation_time
