import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pi_power_manager.cli import main


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = root / "config.json"
        self.state = root / "state.json"
        self.rtc = root / "rtc"
        self.rtc.mkdir()
        (self.rtc / "wakealarm").write_text("0", encoding="ascii")
        (self.rtc / "since_epoch").write_text("1893456000", encoding="ascii")
        self.base = [
            "--config", str(self.config), "--state", str(self.state), "--rtc", str(self.rtc)
        ]
        self.write_config([
            {"power-on": "2030-01-01T08:00:00", "power-down": "2030-01-01T12:00:00"},
            {"power-on": "2030-01-01T13:00:00", "power-down": "2030-01-01T18:00:00"},
        ])

    def tearDown(self):
        self.temporary.cleanup()

    def write_config(self, schedule):
        self.config.write_text(json.dumps({
            "schema_version": 2,
            "timezone": "UTC",
            "minimum_lead_seconds": 60,
            "clock_tolerance_seconds": 5,
            "power_schedule": schedule,
        }), encoding="utf-8")

    def run_cli(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([*self.base, *arguments])
        return code, json.loads(output.getvalue())

    def test_validate_list_and_dry_run_read_json_config(self):
        code, validated = self.run_cli("validate-config")
        self.assertEqual(0, code)
        self.assertTrue(validated["valid"])
        _, listed = self.run_cli("list", "--json")
        self.assertEqual(2, len(listed))
        _, dry_run = self.run_cli(
            "dry-run", "--at", "2030-01-01T12:00:00Z", "--json"
        )
        self.assertTrue(dry_run["would_attempt_shutdown"])
        self.assertEqual("2030-01-01T13:00:00Z", dry_run["next_action"]["wake_at"])

    def test_each_command_reloads_changed_config(self):
        _, listed = self.run_cli("list", "--json")
        self.assertEqual(2, len(listed))
        self.write_config([
            {"power-on": "2030-01-02T08:00:00", "power-down": "2030-01-02T12:00:00"}
        ])
        _, reloaded = self.run_cli("list", "--json")
        self.assertEqual(1, len(reloaded))
        self.assertEqual("2030-01-02T08:00:00", reloaded[0]["power-on"])

    def test_apply_with_simulated_time_never_executes(self):
        code, result = self.run_cli(
            "apply", "--yes", "--at", "2030-01-01T12:00:00Z", "--json"
        )
        self.assertEqual(2, code)
        self.assertEqual("kept_running", result["outcome"])

    def test_validate_requires_an_actual_config_file(self):
        self.config.unlink()
        error = io.StringIO()
        with redirect_stdout(io.StringIO()), patch("sys.stderr", error):
            code = main([*self.base, "validate-config"])
        self.assertEqual(2, code)
        self.assertIn("configuration file not found", error.getvalue())


if __name__ == "__main__":
    unittest.main()
