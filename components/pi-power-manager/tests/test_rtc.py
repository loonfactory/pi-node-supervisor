import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pi_power_manager.errors import AlarmConflictError, ValidationError
from pi_power_manager.rtc import SysfsRTC


class SysfsRTCTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "wakealarm").write_text("0", encoding="ascii")
        self.now = datetime(2030, 1, 1, 12, tzinfo=UTC)
        (self.root / "since_epoch").write_text(str(int(self.now.timestamp())), encoding="ascii")
        self.rtc = SysfsRTC(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_reads_rtc_time_and_empty_alarm(self):
        self.assertEqual(self.now, self.rtc.read_time())
        self.assertIsNone(self.rtc.read_alarm())

    def test_accepts_raspberry_pi_5_rtc_name_with_device_suffix(self):
        (self.root / "name").write_text(
            "rpi-rtc soc@107c000000:rpi_rtc\n", encoding="ascii"
        )

        checks = {item["name"]: item for item in self.rtc.check()}

        self.assertEqual("pass", checks["rtc_driver"]["status"])
        self.assertEqual(
            "rpi-rtc soc@107c000000:rpi_rtc", checks["rtc_driver"]["detail"]
        )

    def test_rejects_unexpected_rtc_driver(self):
        (self.root / "name").write_text("unexpected-rtc\n", encoding="ascii")

        checks = {item["name"]: item for item in self.rtc.check()}

        self.assertEqual("fail", checks["rtc_driver"]["status"])

    def test_sets_and_verifies_future_alarm(self):
        target = self.now + timedelta(minutes=10)
        epoch = self.rtc.set_alarm(
            target, now=self.now, minimum_lead_seconds=60, owned_alarm_epoch=None
        )
        self.assertEqual(int(target.timestamp()), epoch)
        self.assertEqual(epoch, self.rtc.read_alarm())

    def test_refuses_external_alarm(self):
        (self.root / "wakealarm").write_text(str(int((self.now + timedelta(hours=1)).timestamp())))
        with self.assertRaises(AlarmConflictError):
            self.rtc.set_alarm(
                self.now + timedelta(hours=2),
                now=self.now,
                minimum_lead_seconds=60,
                owned_alarm_epoch=None,
            )

    def test_refuses_alarm_inside_minimum_lead(self):
        with self.assertRaises(ValidationError):
            self.rtc.set_alarm(
                self.now + timedelta(seconds=59),
                now=self.now,
                minimum_lead_seconds=60,
                owned_alarm_epoch=None,
            )


if __name__ == "__main__":
    unittest.main()
