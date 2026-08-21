import unittest
from datetime import UTC, datetime, timedelta

from pi_power_manager.errors import ValidationError
from pi_power_manager.models import Configuration, OneShotSchedule, PowerWindow, RuntimeState
from pi_power_manager.scheduler import Scheduler


class ModelAndSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2030, 1, 1, 8, tzinfo=UTC)

    def window(self, on_hours: int, down_hours: int) -> PowerWindow:
        return PowerWindow(
            self.now + timedelta(hours=on_hours),
            self.now + timedelta(hours=down_hours),
        )

    def test_internal_shutdown_cycle_requires_ordered_aware_times(self):
        with self.assertRaises(ValidationError):
            OneShotSchedule("bad", datetime(2030, 1, 1), datetime(2030, 1, 2))
        with self.assertRaises(ValidationError):
            OneShotSchedule("bad", self.now, self.now)

    def test_power_windows_must_be_ordered_and_non_overlapping(self):
        first = self.window(0, 4)
        overlapping = self.window(3, 6)
        with self.assertRaises(ValidationError):
            Configuration(power_schedule=(first, overlapping))
        with self.assertRaises(ValidationError):
            Configuration(power_schedule=(self.window(5, 6), first))

    def test_power_down_uses_following_power_on_as_wake_alarm(self):
        first = self.window(0, 2)
        second = self.window(5, 8)
        third = self.window(10, 12)
        config = Configuration(power_schedule=(first, second, third))
        cycles = config.schedules
        self.assertEqual(2, len(cycles))
        self.assertEqual(first.power_down, cycles[0].shutdown_at)
        self.assertEqual(second.power_on, cycles[0].wake_at)
        self.assertEqual(second.power_down, cycles[1].shutdown_at)
        self.assertEqual(third.power_on, cycles[1].wake_at)

    def test_next_action_is_deterministic_and_due_within_window(self):
        config = Configuration(
            power_schedule=(self.window(0, 2), self.window(5, 8), self.window(10, 12))
        )
        scheduler = Scheduler()
        action = scheduler.next_action(config, RuntimeState(), self.now)
        self.assertEqual(self.now + timedelta(hours=2), action.shutdown_at)
        self.assertFalse(action.due)
        due = scheduler.next_action(config, RuntimeState(), self.now + timedelta(hours=2))
        self.assertTrue(due.due)

    def test_last_power_down_without_following_power_on_is_not_scheduled(self):
        config = Configuration(power_schedule=(self.window(0, 2),))
        self.assertIsNone(Scheduler().next_action(config, RuntimeState(), self.now))

    def test_completed_cycle_is_ignored(self):
        config = Configuration(
            power_schedule=(self.window(0, 2), self.window(5, 8), self.window(10, 12))
        )
        first_id = config.schedules[0].id
        state = RuntimeState(completed_schedule_ids=(first_id,))
        action = Scheduler().next_action(config, state, self.now)
        self.assertEqual(config.schedules[1].id, action.schedule_id)


if __name__ == "__main__":
    unittest.main()

