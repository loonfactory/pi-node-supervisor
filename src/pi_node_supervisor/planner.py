"""Deterministic activity, execution, and power planning."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .errors import PlanningError
from .models import (
    ActivityPlan,
    CaptureActivity,
    CaptureScheduleDefinition,
    DailyPowerKeepSchedule,
    DerivedPlan,
    MaintenanceActivity,
    NodeDefinition,
    PowerManagerConfiguration,
    PowerManagerWindow,
    PowerWindowPlan,
    SensorActivity,
    SupervisorConfiguration,
)
from .registry import NodeRegistry
from .timeutil import format_local_naive, parse_activity_time


@dataclass(slots=True)
class _MutablePowerWindow:
    power_on: datetime
    power_down: datetime
    activity_ids: list[str]
    reasons: list[str]


class Planner:
    def __init__(self, registry: NodeRegistry) -> None:
        self.registry = registry

    def plan(
        self, configuration: SupervisorConfiguration, *, now: datetime | None = None
    ) -> DerivedPlan:
        one_shot_plans = tuple(
            sorted(
                (self._plan_activity(configuration, activity) for activity in configuration.activities),
                key=lambda item: (item.requested_start, item.activity_id),
            )
        )
        schedule_plans: tuple[ActivityPlan, ...] = ()
        if configuration.capture_schedules:
            planning_start = (now or datetime.now(UTC)).astimezone(UTC)
            planning_end = planning_start + timedelta(
                hours=configuration.policies.recurrence_horizon_hours
            )
            schedule_plans = tuple(
                item
                for schedule in configuration.capture_schedules
                for item in self._plan_capture_schedule(
                    configuration, schedule, planning_start, planning_end
                )
            )
        power_keep_plans: tuple[ActivityPlan, ...] = ()
        if configuration.power_keep_schedules:
            planning_start = (now or datetime.now(UTC)).astimezone(UTC)
            planning_end = planning_start + timedelta(
                hours=configuration.policies.recurrence_horizon_hours
            )
            power_keep_plans = tuple(
                item
                for schedule in configuration.power_keep_schedules
                for item in self._plan_power_keep_schedule(
                    schedule, planning_start, planning_end
                )
            )
        activity_plans = tuple(
            sorted(
                (*one_shot_plans, *schedule_plans, *power_keep_plans),
                key=lambda item: (item.requested_start, item.activity_id),
            )
        )
        self._validate_node_conflicts(activity_plans)
        power_windows = self._merge_power_windows(configuration, activity_plans)
        canonical = json.dumps(
            configuration.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return DerivedPlan(
            timezone=configuration.timezone,
            source_hash=hashlib.sha256(canonical).hexdigest(),
            activities=activity_plans,
            power_windows=power_windows,
        )

    def _plan_capture_schedule(
        self,
        configuration: SupervisorConfiguration,
        schedule: CaptureScheduleDefinition,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[ActivityPlan, ...]:
        if schedule.mode == "disabled":
            return ()
        node = self._validate_node_activity(schedule.node, "capture", schedule.profile)
        if schedule.mode == "daily" and not schedule.daily_times:
            return ()
        runtime = schedule.duration_seconds or node.default_runtime_seconds
        footprint = timedelta(
            seconds=(
                configuration.policies.boot_lead_seconds
                + node.boot_ready_seconds
                + node.prepare_seconds
                + runtime
                + node.cleanup_seconds
                + configuration.policies.shutdown_grace_seconds
            )
        )
        search_start = window_start - footprint
        occurrences: list[datetime] = []
        continuous_interval = False

        if schedule.mode == "daily":
            zone = ZoneInfo(schedule.timezone)
            first_date = search_start.astimezone(zone).date()
            final_date = window_end.astimezone(zone).date()
            date = first_date
            while date <= final_date:
                for daily_time in schedule.daily_times:
                    occurrences.append(
                        parse_activity_time(
                            f"{date.isoformat()}T{daily_time.isoformat()}",
                            schedule.timezone,
                            f"capture schedule {schedule.id}",
                        )
                    )
                date += timedelta(days=1)
        else:
            assert schedule.interval_seconds is not None
            assert schedule.anchor_at is not None
            anchor = parse_activity_time(
                schedule.anchor_at,
                schedule.timezone,
                f"capture schedule {schedule.id}.anchor_at",
            )
            # CaptureNode's interval deadline is relative to process start. Powering
            # down between occurrences would reset it on every boot, so interval
            # mode keeps the node powered for the complete rolling horizon.
            continuous_interval = True
            first = max(anchor, search_start)
            if first <= window_end:
                occurrences.extend((first, window_end))

        plans = [
            self._build_plan(
                configuration,
                activity_id=self._occurrence_id(schedule.id, occurrence),
                activity_type="capture-schedule",
                node=node,
                profile=schedule.profile,
                parameters=schedule.parameters,
                requested_start=occurrence,
                requested_end=occurrence + timedelta(seconds=runtime),
            )
            for occurrence in sorted(set(occurrences))
        ]
        plans = [
            plan
            for plan in plans
            if plan.power_down >= window_start and plan.power_on <= window_end
        ]
        if (
            continuous_interval
            and len(plans) > 1
        ):
            first, last = plans[0], plans[-1]
            assert schedule.interval_seconds is not None
            return (
                ActivityPlan(
                    activity_id=schedule.id,
                    activity_type="capture-schedule",
                    node_id=node.id,
                    profile=schedule.profile,
                    parameters=schedule.parameters,
                    requested_start=first.requested_start,
                    requested_end=last.requested_end,
                    prepare_at=first.prepare_at,
                    complete_at=last.complete_at,
                    release_at=last.release_at,
                    power_on=first.power_on,
                    power_down=last.power_down,
                    reasons=(
                        f"interval capture every {schedule.interval_seconds:g}s",
                        "continuous power preserves CaptureNode's process-relative interval timer",
                        "CaptureNode executes captures with its native scheduler",
                    ),
                ),
            )
        return tuple(plans)

    def _plan_power_keep_schedule(
        self,
        schedule: DailyPowerKeepSchedule,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[ActivityPlan, ...]:
        zone = ZoneInfo(schedule.timezone)
        date = window_start.astimezone(zone).date()
        final_date = window_end.astimezone(zone).date()
        plans: list[ActivityPlan] = []
        while date <= final_date:
            start = parse_activity_time(
                f"{date.isoformat()}T{schedule.start.isoformat()}",
                schedule.timezone,
                f"power keep schedule {schedule.id}.start",
            )
            end = parse_activity_time(
                f"{date.isoformat()}T{schedule.end.isoformat()}",
                schedule.timezone,
                f"power keep schedule {schedule.id}.end",
            )
            if end >= window_start and start <= window_end:
                plans.append(
                    ActivityPlan(
                        activity_id=self._occurrence_id(schedule.id, start),
                        activity_type="power-keep",
                        node_id="supervisor",
                        profile=None,
                        requested_start=start,
                        requested_end=end,
                        prepare_at=start,
                        complete_at=end,
                        release_at=end,
                        power_on=start,
                        power_down=end,
                        reasons=("daily maintenance power keep",),
                    )
                )
            date += timedelta(days=1)
        return tuple(plans)

    @staticmethod
    def _occurrence_id(schedule_id: str, occurrence: datetime) -> str:
        timestamp = occurrence.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        digest = hashlib.sha256(schedule_id.encode("utf-8")).hexdigest()[:8]
        return f"{schedule_id[:58]}-{digest}-{timestamp}"

    def power_manager_configuration(
        self,
        configuration: SupervisorConfiguration,
        plan: DerivedPlan | None = None,
    ) -> PowerManagerConfiguration:
        derived = plan or self.plan(configuration)
        return PowerManagerConfiguration(
            timezone=configuration.timezone,
            minimum_lead_seconds=configuration.policies.minimum_lead_seconds,
            clock_tolerance_seconds=configuration.policies.clock_tolerance_seconds,
            power_schedule=tuple(
                PowerManagerWindow(
                    **{
                        "power-on": format_local_naive(window.power_on, configuration.timezone),
                        "power-down": format_local_naive(window.power_down, configuration.timezone),
                    }
                )
                for window in derived.power_windows
            ),
        )

    def _plan_activity(
        self,
        configuration: SupervisorConfiguration,
        activity: CaptureActivity | SensorActivity | MaintenanceActivity,
    ) -> ActivityPlan:
        if isinstance(activity, MaintenanceActivity):
            return self._plan_maintenance(configuration, activity)
        node = self._validate_node_activity(activity.node, activity.type, activity.profile)
        requested_start = parse_activity_time(
            activity.at, configuration.timezone, f"activity {activity.id}.at"
        )
        runtime = activity.duration_seconds or node.default_runtime_seconds
        requested_end = requested_start + timedelta(seconds=runtime)
        return self._build_plan(
            configuration,
            activity_id=activity.id,
            activity_type=activity.type,
            node=node,
            profile=activity.profile,
            parameters=activity.parameters,
            requested_start=requested_start,
            requested_end=requested_end,
        )

    def _plan_maintenance(
        self,
        configuration: SupervisorConfiguration,
        activity: MaintenanceActivity,
    ) -> ActivityPlan:
        requested_start = parse_activity_time(
            activity.start, configuration.timezone, f"activity {activity.id}.start"
        )
        requested_end = parse_activity_time(
            activity.end, configuration.timezone, f"activity {activity.id}.end"
        )
        if requested_end <= requested_start:
            raise PlanningError(f"activity {activity.id}.end must be later than start")
        if activity.node is None:
            node = NodeDefinition(
                id="supervisor",
                kind="maintenance",
                activity_types=("maintenance-window",),
                profiles=(),
                default_runtime_seconds=1,
                required=True,
            )
            if activity.profile is not None:
                raise PlanningError(
                    f"activity {activity.id} cannot set profile without a node"
                )
        else:
            node = self._validate_node_activity(
                activity.node, activity.type, activity.profile
            )
        return self._build_plan(
            configuration,
            activity_id=activity.id,
            activity_type=activity.type,
            node=node,
            profile=activity.profile,
            parameters={},
            requested_start=requested_start,
            requested_end=requested_end,
        )

    def _validate_node_activity(
        self, node_id: str, activity_type: str, profile: str | None
    ) -> NodeDefinition:
        node = self.registry.require(node_id)
        if activity_type not in node.activity_types:
            raise PlanningError(
                f"node {node_id} does not support activity type {activity_type}"
            )
        if profile is not None and profile not in node.profiles:
            raise PlanningError(
                f"node {node_id} does not provide profile {profile}"
            )
        return node

    @staticmethod
    def _build_plan(
        configuration: SupervisorConfiguration,
        *,
        activity_id: str,
        activity_type: str,
        node: NodeDefinition,
        profile: str | None,
        parameters: dict,
        requested_start,
        requested_end,
    ) -> ActivityPlan:
        prepare_at = requested_start - timedelta(seconds=node.prepare_seconds)
        complete_at = requested_end
        release_at = complete_at + timedelta(seconds=node.cleanup_seconds)
        power_on = prepare_at - timedelta(
            seconds=configuration.policies.boot_lead_seconds + node.boot_ready_seconds
        )
        if activity_type in {"capture", "capture-schedule"}:
            configured_power_down = requested_start + timedelta(
                seconds=configuration.policies.shutdown_grace_seconds
            )
            power_down = max(release_at, configured_power_down)
            shutdown_reason = (
                f"shutdown grace {configuration.policies.shutdown_grace_seconds}s "
                "from scheduled capture, bounded by node release"
            )
        else:
            power_down = release_at + timedelta(
                seconds=configuration.policies.shutdown_grace_seconds
            )
            shutdown_reason = (
                f"node cleanup {node.cleanup_seconds}s and shutdown grace "
                f"{configuration.policies.shutdown_grace_seconds}s"
            )
        reasons = (
            f"boot lead {configuration.policies.boot_lead_seconds}s",
            f"node {node.id} ready {node.boot_ready_seconds}s and prepare {node.prepare_seconds}s",
            f"activity runtime {(requested_end - requested_start).total_seconds():g}s",
            shutdown_reason,
        )
        return ActivityPlan(
            activity_id=activity_id,
            activity_type=activity_type,
            node_id=node.id,
            profile=profile,
            parameters=parameters,
            requested_start=requested_start,
            requested_end=requested_end,
            prepare_at=prepare_at,
            complete_at=complete_at,
            release_at=release_at,
            power_on=power_on,
            power_down=power_down,
            reasons=reasons,
        )

    @staticmethod
    def _validate_node_conflicts(activity_plans: tuple[ActivityPlan, ...]) -> None:
        by_node: dict[str, list[ActivityPlan]] = {}
        for item in activity_plans:
            if item.node_id == "supervisor":
                continue
            by_node.setdefault(item.node_id, []).append(item)
        for node_id, items in by_node.items():
            ordered = sorted(items, key=lambda item: (item.prepare_at, item.activity_id))
            for previous, current in zip(ordered, ordered[1:]):
                if current.prepare_at < previous.release_at:
                    raise PlanningError(
                        f"node {node_id} activities overlap: "
                        f"{previous.activity_id} and {current.activity_id}"
                    )

    @staticmethod
    def _merge_power_windows(
        configuration: SupervisorConfiguration,
        activity_plans: tuple[ActivityPlan, ...],
    ) -> tuple[PowerWindowPlan, ...]:
        if not activity_plans:
            return ()
        threshold = configuration.policies.merge_power_gaps_shorter_than_seconds
        ordered = sorted(activity_plans, key=lambda item: (item.power_on, item.activity_id))
        merged: list[_MutablePowerWindow] = []
        for item in ordered:
            if not merged:
                merged.append(
                    _MutablePowerWindow(
                        item.power_on,
                        item.power_down,
                        [item.activity_id],
                        [f"activity {item.activity_id}"],
                    )
                )
                continue
            current = merged[-1]
            gap_seconds = (item.power_on - current.power_down).total_seconds()
            if item.power_on <= current.power_down or gap_seconds < threshold:
                if item.power_on <= current.power_down:
                    reason = f"merged overlapping activity {item.activity_id}"
                else:
                    reason = (
                        f"merged {gap_seconds:g}s gap before {item.activity_id}; "
                        f"policy threshold is {threshold}s"
                    )
                current.power_down = max(current.power_down, item.power_down)
                current.activity_ids.append(item.activity_id)
                current.reasons.append(reason)
            else:
                merged.append(
                    _MutablePowerWindow(
                        item.power_on,
                        item.power_down,
                        [item.activity_id],
                        [f"activity {item.activity_id}"],
                    )
                )
        result: list[PowerWindowPlan] = []
        for index, item in enumerate(merged):
            next_power_on = (
                merged[index + 1].power_on if index + 1 < len(merged) else None
            )
            reasons = list(item.reasons)
            if next_power_on is None:
                reasons.append(
                    "automatic shutdown is blocked because no later power window provides an RTC wake time"
                )
            else:
                reasons.append("next power window provides the RTC wake time")
            result.append(
                PowerWindowPlan(
                    power_on=item.power_on,
                    power_down=item.power_down,
                    activity_ids=tuple(item.activity_ids),
                    next_power_on=next_power_on,
                    automatic_shutdown_possible=next_power_on is not None,
                    reasons=tuple(reasons),
                )
            )
        return tuple(result)
