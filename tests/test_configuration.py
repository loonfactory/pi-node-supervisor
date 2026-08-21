import unittest
from datetime import UTC, datetime

from pi_power_manager.errors import ValidationError
from pi_power_manager.models import Configuration


class DeclarativeConfigurationTests(unittest.TestCase):
    def config(self, timezone, schedule):
        return Configuration.from_dict({
            "schema_version": 2,
            "timezone": timezone,
            "minimum_lead_seconds": 60,
            "clock_tolerance_seconds": 5,
            "power_schedule": schedule,
        })

    def test_local_times_use_configured_iana_timezone(self):
        config = self.config("Asia/Seoul", [
            {"power-on": "2030-01-01T08:00:00", "power-down": "2030-01-01T12:00:00"},
            {"power-on": "2030-01-01T13:00:00", "power-down": "2030-01-01T18:00:00"},
        ])
        self.assertEqual(datetime(2029, 12, 31, 23, tzinfo=UTC), config.power_schedule[0].power_on)
        self.assertEqual(datetime(2030, 1, 1, 4, tzinfo=UTC), config.schedules[0].wake_at)
        self.assertEqual("2030-01-01T08:00:00", config.to_dict()["power_schedule"][0]["power-on"])

    def test_explicit_offset_is_accepted(self):
        config = self.config("Asia/Seoul", [
            {"power-on": "2030-01-01T08:00:00+09:00", "power-down": "2030-01-01T12:00:00+09:00"}
        ])
        self.assertEqual(datetime(2029, 12, 31, 23, tzinfo=UTC), config.power_schedule[0].power_on)

    def test_ambiguous_and_nonexistent_dst_times_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "does not exist"):
            self.config("America/New_York", [
                {"power-on": "2030-03-10T02:30:00", "power-down": "2030-03-10T04:00:00"}
            ])
        with self.assertRaisesRegex(ValidationError, "ambiguous"):
            self.config("America/New_York", [
                {"power-on": "2030-11-03T01:30:00", "power-down": "2030-11-03T03:00:00"}
            ])

    def test_unknown_keys_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "unknown power_schedule item keys"):
            self.config("UTC", [
                {"power-on": "2030-01-01T08:00:00", "power-down": "2030-01-01T12:00:00", "enabled": True}
            ])


if __name__ == "__main__":
    unittest.main()
