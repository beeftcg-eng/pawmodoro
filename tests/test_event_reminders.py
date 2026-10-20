"""Reminders for calendar events with a time (Storage.check_due_event_reminders)."""
from datetime import datetime

from support import StorageTestCase


class EventReminderTests(StorageTestCase):
    NOW = datetime(2026, 10, 20, 9, 0)

    def check(self, now=None):
        return [e["title"] for e in self.storage.check_due_event_reminders(now or self.NOW)]

    def test_due_at_its_start_time_once(self):
        self.check()  # the first check after an update lets past ones go
        self.storage.save_event("Dentist", "2026-10-20", start_time="09:30")
        self.storage.save_event("All day", "2026-10-20")
        self.storage.save_event("Tomorrow", "2026-10-21", start_time="08:00")
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(datetime(2026, 10, 20, 9, 30)), ["Dentist"])
        self.assertEqual(self.check(datetime(2026, 10, 20, 9, 31)), [], "only once")

    def test_moving_it_to_another_day_reminds_again(self):
        self.check()
        event = self.storage.save_event("Call", "2026-10-20", start_time="08:00")
        self.assertEqual(self.check(), ["Call"])
        self.storage.save_event("Call", "2026-10-21", start_time="08:00", event_id=event["id"])
        self.assertEqual(self.check(datetime(2026, 10, 21, 8, 0)), ["Call"])

    def test_a_day_late_is_let_go(self):
        self.check(datetime(2026, 10, 17, 9, 0))
        self.storage.save_event("Missed", "2026-10-18", start_time="08:00")
        self.assertEqual(self.check(), [])

    def test_already_past_on_the_first_check_is_not_announced(self):
        self.storage.save_event("Earlier today", "2026-10-20", start_time="07:00")
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(), [])

    def test_household_events_remind_too(self):
        self.check()
        self.storage.data["household"] = {"id": "h1", "tasks": [], "members": [], "events": [
            {"id": "he1", "title": "Dinner", "start_date": "2026-10-20", "end_date": "2026-10-20",
             "start_time": "08:30"}]}
        self.assertEqual(self.check(), ["Dinner"])
