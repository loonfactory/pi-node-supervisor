import unittest
from datetime import UTC, datetime, timedelta

from pi_power_manager.errors import RTCError
from pi_power_manager.models import Configuration, OneShotSchedule, RuntimeState
from pi_power_manager.power import DryRunPowerAdapter, PowerCoordinator


class FakeRTC:
    def __init__(self, now, *, fail=False):
        self.now = now
        self.fail = fail
        self.events = []

    def read_time(self):
        self.events.append("read_time")
        return self.now

    def set_alarm(self, target, **kwargs):
        self.events.append("set_alarm")
        if self.fail:
            raise RTCError("write_alarm", "fake", "injected failure")
        return int(target.timestamp())


class EventPower(DryRunPowerAdapter):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def poweroff(self):
        self.events.append("poweroff")
        super().poweroff()


class PowerCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2030, 1, 1, 12, tzinfo=UTC)
        self.schedule = OneShotSchedule(
            "test", self.now, self.now + timedelta(hours=1)
        )
        self.config = Configuration(minimum_lead_seconds=60, clock_tolerance_seconds=5)

    def test_alarm_is_verified_and_persisted_before_poweroff(self):
        rtc = FakeRTC(self.now)
        power = EventPower(rtc.events)
        persisted = []
        result, state = PowerCoordinator(rtc, power).execute(
            self.schedule, self.config, RuntimeState(), self.now,
            persist_prepared=lambda value: (rtc.events.append("persist"), persisted.append(value)),
        )
        self.assertEqual("shutdown_requested", result.outcome)
        self.assertEqual(["read_time", "set_alarm", "persist", "poweroff"], rtc.events)
        self.assertEqual(1, power.request_count)
        self.assertEqual(int(self.schedule.wake_at.timestamp()), persisted[0].owned_alarm_epoch)
        self.assertIn("test", state.completed_schedule_ids)

    def test_rtc_failure_keeps_running(self):
        rtc = FakeRTC(self.now, fail=True)
        power = EventPower(rtc.events)
        result, state = PowerCoordinator(rtc, power).execute(
            self.schedule, self.config, RuntimeState(), self.now
        )
        self.assertEqual("kept_running", result.outcome)
        self.assertEqual(0, power.request_count)
        self.assertEqual("RTCError", result.details["error_type"])
        self.assertIsNotNone(state.last_error)

    def test_clock_mismatch_keeps_running(self):
        rtc = FakeRTC(self.now + timedelta(seconds=6))
        power = EventPower(rtc.events)
        result, _ = PowerCoordinator(rtc, power).execute(
            self.schedule, self.config, RuntimeState(), self.now
        )
        self.assertEqual("kept_running", result.outcome)
        self.assertEqual(0, power.request_count)

    def test_suspension_and_duplicate_keep_running(self):
        for state in (
            RuntimeState(suspended=True),
            RuntimeState(completed_schedule_ids=("test",)),
        ):
            rtc = FakeRTC(self.now)
            power = EventPower(rtc.events)
            result, _ = PowerCoordinator(rtc, power).execute(
                self.schedule, self.config, state, self.now
            )
            self.assertEqual("kept_running", result.outcome)
            self.assertEqual(0, power.request_count)


if __name__ == "__main__":
    unittest.main()

