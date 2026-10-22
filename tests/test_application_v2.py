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
