import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pi_power_manager.diagnostics import diagnose


class PassingRTC:
    def check(self):
        return []


class DiagnosticsStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "config.json"
        self.state_path = self.root / "state.json"
        self.config_path.write_text("{}", encoding="utf-8")
        self.state_path.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    @patch("pi_power_manager.diagnostics.shutil.which", return_value="/usr/bin/systemctl")
    @patch("pi_power_manager.diagnostics.os.access")
    def test_config_needs_read_access_while_state_needs_read_write(
        self, access, _which
    ):
        def access_check(path, mode):
            if path == self.config_path:
                return mode == os.R_OK
            if path == self.state_path:
                return mode == os.R_OK | os.W_OK
            return True

        access.side_effect = access_check

        result = diagnose(PassingRTC(), self.config_path, self.state_path)
        checks = {item["name"]: item for item in result["checks"]}

        self.assertEqual("pass", checks["config_storage"]["status"])
        self.assertEqual("pass", checks["state_storage"]["status"])
        access.assert_any_call(self.config_path, os.R_OK)
        access.assert_any_call(self.state_path, os.R_OK | os.W_OK)

    @patch("pi_power_manager.diagnostics.shutil.which", return_value="/usr/bin/systemctl")
    @patch("pi_power_manager.diagnostics.os.access", return_value=True)
    def test_missing_config_fails_instead_of_treating_parent_as_storage(
        self, _access, _which
    ):
        self.config_path.unlink()

        result = diagnose(PassingRTC(), self.config_path, self.state_path)
        checks = {item["name"]: item for item in result["checks"]}

        self.assertEqual("fail", checks["config_storage"]["status"])


if __name__ == "__main__":
    unittest.main()
