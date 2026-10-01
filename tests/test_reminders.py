"""Checklist reminders: daily-at-a-time, every N hours, quiet hours, snooze."""
from datetime import datetime, timedelta
from unittest import mock

from support import StorageTestCase


def frozen_now(moment):
    """Patches storage's datetime.now() to `moment`."""
    real = __import__("datetime").datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return moment

    return mock.patch("storage.datetime", Frozen)


class DailyReminderTests(StorageTestCase):
    def test_fires_once_a_day_after_its_time(self):
        task = self.storage.add_task("Walk the dogs", "daily")
        self.storage.set_task_reminder(task["id"], "09:00")
        day = datetime(2026, 10, 1)
        with frozen_now(day.replace(hour=8, minute=59)):
            self.assertEqual(self.storage.check_due_reminders(), [])
        with frozen_now(day.replace(hour=9)):
            self.assertEqual([t["id"] for t in self.storage.check_due_reminders()], [task["id"]])
        with frozen_now(day.replace(hour=15)):
            self.assertEqual(self.storage.check_due_reminders(), [])

    def test_not_once_ticked_off(self):
        task = self.storage.add_task("Walk the dogs", "daily")
        self.storage.set_task_reminder(task["id"], "00:00")
        self.storage.set_task_done(task["id"], True)
        self.assertEqual(self.storage.check_due_reminders(), [])

    def test_specific_day_task_only_on_its_day(self):
        monday = datetime(2026, 9, 28, 12)
        task = self.storage.add_task("Trash", "weekday", weekday=monday.weekday())
        self.storage.set_task_reminder(task["id"], "07:00")
        with frozen_now(monday + timedelta(days=1)):
            self.assertEqual(self.storage.check_due_reminders(), [])
        with frozen_now(monday + timedelta(days=7)):
            self.assertEqual(len(self.storage.check_due_reminders()), 1)


class RepeatingReminderTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.task = self.storage.add_task("Drink water", "daily")
        self.storage.set_task_reminder(self.task["id"], None, every_hours=8)
        self.set_at = datetime.fromisoformat(self.task["last_reminded_at"])

    def test_every_n_hours_from_when_it_was_set(self):
        self.assertIsNone(self.task["reminder_time"])
        with frozen_now(self.set_at + timedelta(hours=7, minutes=59)):
            self.assertEqual(self.storage.check_due_reminders(), [])
        with frozen_now(self.set_at + timedelta(hours=8)):
            self.assertEqual(len(self.storage.check_due_reminders()), 1)
        with frozen_now(self.set_at + timedelta(hours=9)):
            self.assertEqual(self.storage.check_due_reminders(), [])  # counts from the last one now
        with frozen_now(self.set_at + timedelta(hours=16)):
            self.assertEqual(len(self.storage.check_due_reminders()), 1)

    def test_daily_time_replaces_it(self):
        self.storage.set_task_reminder(self.task["id"], "10:00")
        self.assertIsNone(self.task["reminder_every_h"])
        self.assertEqual(self.task["reminder_time"], "10:00")

    def test_clock_moved_back_does_not_silence_it(self):
        self.task["last_reminded_at"] = (datetime.now() + timedelta(days=2)).isoformat()
        self.assertEqual(len(self.storage.check_due_reminders()), 1)

    def test_quiet_hours_hold_it_then_catch_up_once(self):
        self.storage.set_reminder_settings(quiet_enabled=True, quiet_start="22:00", quiet_end="08:00")
        night = (self.set_at + timedelta(days=1)).replace(hour=2, minute=0)
        with frozen_now(night):
            self.assertEqual(self.storage.check_due_reminders(), [])
        with frozen_now(night.replace(hour=8)):
            self.assertEqual(len(self.storage.check_due_reminders()), 1)
        with frozen_now(night.replace(hour=8, minute=30)):
            self.assertEqual(self.storage.check_due_reminders(), [])

    def test_quiet_hours_window(self):
        st = self.storage
        st.set_reminder_settings(quiet_enabled=True, quiet_start="22:00", quiet_end="08:00")
        at = lambda h, m=0: datetime(2026, 10, 1, h, m)  # noqa: E731
        self.assertTrue(st._in_quiet_hours(at(23)))
        self.assertTrue(st._in_quiet_hours(at(0)))
        self.assertFalse(st._in_quiet_hours(at(8)))
        self.assertFalse(st._in_quiet_hours(at(21, 59)))
        st.set_reminder_settings(quiet_start="13:00", quiet_end="14:00")
        self.assertTrue(st._in_quiet_hours(at(13, 30)))
        self.assertFalse(st._in_quiet_hours(at(14)))
        st.set_reminder_settings(quiet_enabled=False)
        self.assertFalse(st._in_quiet_hours(at(13, 30)))

    def test_survives_a_restart(self):
        reopened = self.new_storage()
        task = reopened._find_task(self.task["id"])
        self.assertEqual(task["reminder_every_h"], 8)


class SnoozeTests(StorageTestCase):
    def test_snooze_comes_back_once_and_holds_the_regular_one(self):
        task = self.storage.add_task("Call mum", "daily")
        self.storage.set_task_reminder(task["id"], "00:00")
        self.storage.check_due_reminders()  # today's regular one fired
        self.storage.snooze_task_reminder(task["id"], 15)
        until = datetime.fromisoformat(task["snoozed_until"])
        with frozen_now(until - timedelta(minutes=1)):
            self.assertEqual(self.storage.check_due_reminders(), [])
        with frozen_now(until):
            self.assertEqual(len(self.storage.check_due_reminders()), 1)
        self.assertIsNone(task["snoozed_until"])

    def test_ticking_off_cancels_a_snooze(self):
        task = self.storage.add_task("Call mum", "daily")
        self.storage.snooze_task_reminder(task["id"], 15)
        self.storage.set_task_done(task["id"], True)
        self.storage.check_due_reminders()
        self.assertIsNone(task["snoozed_until"])
