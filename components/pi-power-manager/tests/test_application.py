import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from pi_power_manager.application import Application
from pi_power_manager.models import Configuration, PowerWindow
from pi_power_manager.power import DryRunPowerAdapter
from pi_power_manager.storage import JsonStore


class FakeRTC:
    def __init__(self, now):
        self.now = now
        self.alarm = None
        self.set_count = 0

    def check(self):
        return [{"name": "fake_rtc", "status": "pass", "detail": "test"}]

    def read_time(self):
        return self.now

    def read_alarm(self):
        return self.alarm

    def set_alarm(self, target, **kwargs):
        self.set_count += 1
        self.alarm = int(target.timestamp())
        return self.alarm

    def clear_alarm(self, *, owned_alarm_epoch):
        self.alarm = None


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = JsonStore(root / "config.json", root / "state.json")
        self.now = datetime(2030, 1, 1, 12, tzinfo=UTC)
        self.rtc = FakeRTC(self.now)
        self.power = DryRunPowerAdapter()
        self.app = Application(self.store, self.rtc, self.power)

    def tearDown(self):
        self.temporary.cleanup()

    def configuration(self, *, include_next=True):
        windows = [PowerWindow(self.now - timedelta(hours=1), self.now)]
        if include_next:
            windows.append(
                PowerWindow(self.now + timedelta(hours=1), self.now + timedelta(hours=2))
            )
        return Configuration(power_schedule=tuple(windows))

    def test_due_power_down_executes_with_next_power_on(self):
        config = self.configuration()
        self.store.save_config(config)
        with patch.object(
            self.app, "diagnose", return_value={"automatic_shutdown_ready": True, "checks": []}
        ):
            result = self.app.apply_due(self.now)
        self.assertEqual("shutdown_requested", result["outcome"])
        self.assertEqual(1, self.rtc.set_count)
        self.assertEqual(1, self.power.request_count)
        self.assertEqual(
            int(config.power_schedule[1].power_on.timestamp()), self.rtc.alarm
        )

    def test_failed_diagnostics_prevent_rtc_and_power_changes(self):
        self.store.save_config(self.configuration())
        failed = {
            "automatic_shutdown_ready": False,
            "checks": [{"name": "hardware", "status": "fail", "detail": "not a Pi 5"}],
        }
        with patch.object(self.app, "diagnose", return_value=failed):
            result = self.app.apply_due(self.now)
        self.assertEqual("kept_running", result["outcome"])
        self.assertEqual(0, self.rtc.set_count)
        self.assertEqual(0, self.power.request_count)
        self.assertEqual("diagnostics", self.store.load_state().last_error["stage"])

    def test_last_power_down_without_next_power_on_keeps_running(self):
        self.store.save_config(self.configuration(include_next=False))
        result = self.app.apply_due(self.now)
        self.assertEqual("kept_running", result["outcome"])
        self.assertEqual("no schedule is due", result["reason"])
        self.assertEqual(0, self.rtc.set_count)
        self.assertEqual(0, self.power.request_count)


if __name__ == "__main__":
    unittest.main()

