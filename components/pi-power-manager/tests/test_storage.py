import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pi_power_manager.errors import StorageError
from pi_power_manager.models import Configuration, PowerWindow, RuntimeState
from pi_power_manager.storage import JsonStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = JsonStore(root / "config.json", root / "state.json")

    def tearDown(self):
        self.temporary.cleanup()

    def test_atomic_save_and_backup_recovery(self):
        now = datetime(2030, 1, 1, tzinfo=UTC)
        first = Configuration(
            power_schedule=(PowerWindow(now, now + timedelta(hours=1)),)
        )
        second = replace(first, minimum_lead_seconds=120)
        self.store.save_config(first)
        self.store.save_config(second)
        self.store.config_path.write_text("{broken", encoding="utf-8")
        recovered = self.store.load_config()
        self.assertEqual(60, recovered.minimum_lead_seconds)
        self.assertEqual(now, recovered.power_schedule[0].power_on)

    def test_unknown_configuration_key_is_rejected(self):
        self.store.config_path.write_text(
            json.dumps({"schema_version": 2, "timezone": "UTC", "mystery": True}),
            encoding="utf-8",
        )
        with self.assertRaises(StorageError):
            self.store.load_config()

    def test_missing_schema_version_and_wrong_types_are_rejected(self):
        for raw in (
            {"timezone": "UTC", "power_schedule": []},
            {
                "schema_version": 2,
                "timezone": "UTC",
                "minimum_lead_seconds": "60",
                "power_schedule": [],
            },
        ):
            self.store.config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(StorageError):
                self.store.load_config()

    def test_corrupt_state_without_backup_disables_shutdown(self):
        self.store.state_path.write_text("not json", encoding="utf-8")
        state = self.store.load_state()
        self.assertFalse(state.automatic_shutdown_enabled)
        self.assertEqual("state_load", state.last_error["stage"])

    def test_saved_files_are_private(self):
        self.store.save_state(RuntimeState())
        mode = self.store.state_path.stat().st_mode & 0o777
        self.assertEqual(0o600, mode)


if __name__ == "__main__":
    unittest.main()

