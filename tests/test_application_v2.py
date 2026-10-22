from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from pi_node_supervisor.application_v2 import RuntimeControllerV2
from pi_node_supervisor.revision_v2 import RevisionStoreV2
from pi_node_supervisor.service_v2 import SupervisorServiceV2


def test_manual_capture_is_wrapped_in_power_override(tmp_path: Path) -> None:
    service = SupervisorServiceV2(
        RevisionStoreV2(tmp_path / "state"),
        active_configuration_path=tmp_path / "config.json",
        generated_capture_directory=tmp_path / "capture-nodes",
        power_configuration_path=tmp_path / "power.json",
        local_timezone=timezone.utc,
        timezone_name="UTC",
    )
    controller = RuntimeControllerV2(
        service,
        capture_node_executable=None,
        staging_directory=tmp_path / "staging",
    )
    calls = []

    class Power:
        def begin_manual_override(self):
            calls.append("begin")

        def end_manual_override(self, now):
            assert isinstance(now, datetime)
            calls.append("end")

    class Engine:
        camera_workers = {"rgb": object()}

        async def capture_now(self, device_id, parameters):
            assert calls == ["begin"]
            calls.append((device_id, parameters))
            return "captured"

    controller.power = Power()
    controller.engine = Engine()

    result = asyncio.run(controller.capture_one("rgb", {"quality": 90}))

    assert result == "captured"
    assert calls == ["begin", ("rgb", {"quality": 90}), "end"]


def test_configuration_activation_waits_for_capture(tmp_path, monkeypatch):
    service = SupervisorServiceV2(
        RevisionStoreV2(tmp_path / "state"),
        active_configuration_path=tmp_path / "config.json",
        generated_capture_directory=tmp_path / "capture-nodes",
        power_configuration_path=tmp_path / "power.json",
        local_timezone=timezone.utc,
        timezone_name="UTC",
    )
    controller = RuntimeControllerV2(
        service, capture_node_executable=None, staging_directory=tmp_path / "staging"
    )
    events = []

    async def exercise():
        started, finish = asyncio.Event(), asyncio.Event()

        class Engine:
            async def capture_now(self, device_id, parameters):
                events.append("capture-start")
                started.set()
                await finish.wait()
                events.append("capture-complete")

        controller.engine = Engine()
        original_apply = service.apply_document

        def apply(document):
            events.append("apply")
            return original_apply(document)

        monkeypatch.setattr(service, "apply_document", apply)
        monkeypatch.setattr(controller, "_reload", lambda: events.append("reload"))
        capture = asyncio.create_task(controller.capture_one("rgb"))
        await started.wait()
        applying = asyncio.create_task(controller.apply_revision({"schemaVersion": 2}))
        await asyncio.sleep(0)
        assert events == ["capture-start"]
        finish.set()
        await asyncio.wait_for(asyncio.gather(capture, applying), 2)
        assert events == ["capture-start", "capture-complete", "apply", "reload"]

    asyncio.run(exercise())


def test_apply_failure_restores_previous_revision(tmp_path, monkeypatch):
    import pytest
    from pi_node_supervisor.errors import ApplyError

    service = SupervisorServiceV2(
        RevisionStoreV2(tmp_path / "state"),
        active_configuration_path=tmp_path / "config.json",
        generated_capture_directory=tmp_path / "capture-nodes",
        power_configuration_path=tmp_path / "power.json",
        local_timezone=timezone.utc,
        timezone_name="UTC",
    )
    controller = RuntimeControllerV2(
        service, capture_node_executable=None, staging_directory=tmp_path / "staging"
    )
    previous = service.apply_document({"schemaVersion": 2})
    reloads = []

    def reload():
        reloads.append(service.active_resolution()[0])
        if len(reloads) == 1:
            raise RuntimeError("device unavailable")

    monkeypatch.setattr(controller, "_reload", reload)
    with pytest.raises(ApplyError, match="device unavailable"):
        asyncio.run(controller.apply_revision({"schemaVersion": 2, "power": {"minOffSeconds": 600}}))
    assert reloads[0] != previous["id"]
    assert reloads[1] == previous["id"]
    assert service.active_resolution()[0] == previous["id"]
