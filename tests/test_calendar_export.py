"""calendar_export.py: the .ics file View -> Export to calendar writes."""
import unittest
from datetime import date, datetime

import support  # noqa: F401  (puts the app on sys.path)
import calendar_export

TODAY = date(2026, 10, 12)  # a Monday
NOW = datetime(2026, 10, 12, 8, 0, 0)


def events(ics):
    """[{property: value}] per VEVENT, after unfolding the lines."""
    unfolded = ics.replace("\r\n ", "")
    found, current = [], None
    for line in unfolded.split("\r\n"):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            found.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key] = value
    return found


class CalendarExportTests(unittest.TestCase):
    def build(self, tasks, household=None):
        text, count = calendar_export.build_ics(tasks, household, today=TODAY, now=NOW)
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n"))
        self.assertEqual(len(events(text)), count)
        return text, events(text)

    def test_what_goes_in_and_what_stays_out(self):
        tasks = [
            {"id": "a", "text": "Taxes, finally", "recurrence": "once", "due_date": "2026-10-20", "note": "receipts\nbox"},
            {"id": "b", "text": "Rent", "recurrence": "monthly", "month_day": 1},
            {"id": "c", "text": "Trash", "recurrence": "weekday", "weekday": 3},
            {"id": "d", "text": "Meds", "recurrence": "daily", "reminder_time": "09:00"},
            {"id": "e", "text": "Walk", "recurrence": "daily"},
            {"id": "f", "text": "Plain one-off", "recurrence": "once"},
            {"id": "g", "text": "Done already", "recurrence": "once", "due_date": "2026-10-01", "completed_today": True},
            {"id": "h", "text": "Card", "recurrence": "once", "due_date": "2026-10-20", "source": "wishlist"},
        ]
        _, found = self.build(tasks)
        by_uid = {e["UID"]: e for e in found}
        self.assertEqual(sorted(by_uid), ["task-a@pawmodoro", "task-b@pawmodoro", "task-c@pawmodoro", "task-d@pawmodoro"])
        self.assertEqual(by_uid["task-a@pawmodoro"]["SUMMARY"], "Taxes\\, finally")
        self.assertEqual(by_uid["task-a@pawmodoro"]["DESCRIPTION"], "receipts\\nbox")
        self.assertEqual(by_uid["task-a@pawmodoro"]["DTSTART;VALUE=DATE"], "20261020")
        self.assertEqual(by_uid["task-a@pawmodoro"]["DTEND;VALUE=DATE"], "20261021")
        self.assertEqual(by_uid["task-b@pawmodoro"]["RRULE"], "FREQ=MONTHLY;BYMONTHDAY=1")
        self.assertEqual(by_uid["task-c@pawmodoro"]["RRULE"], "FREQ=WEEKLY;BYDAY=TH")
        self.assertEqual(by_uid["task-c@pawmodoro"]["DTSTART;VALUE=DATE"], "20261015", "the next Thursday")
        self.assertEqual(by_uid["task-d@pawmodoro"]["DTSTART"], "20261012T090000")
        self.assertEqual(by_uid["task-d@pawmodoro"]["DTEND"], "20261012T091500")
        self.assertEqual(by_uid["task-d@pawmodoro"]["DTSTAMP"], "20261012T080000Z")

    def test_end_of_month_days_fall_back_like_the_app(self):
        _, found = self.build([{"id": "x", "text": "Pay", "recurrence": "monthly", "month_day": 31}])
        self.assertEqual(found[0]["RRULE"], "FREQ=MONTHLY;BYMONTHDAY=28,29,30,31;BYSETPOS=-1")
        self.assertEqual(found[0]["DTSTART;VALUE=DATE"], "20260930", "September's last day")

    def test_shared_items(self):
        household = {"tasks": [
            {"id": "s1", "text": "Bins", "recurrence": "weekly", "weekday": 1, "due_time": "19:30"},
            {"id": "s2", "text": "Vet", "recurrence": "once", "due_date": "2026-11-02", "due_time": "10:00"},
            {"id": "s3", "text": "Milk", "recurrence": "once"},
            {"id": "s4", "text": "Water plants", "recurrence": "daily"},
            {"id": "s5", "text": "Rent", "recurrence": "monthly", "month_day": 15},
        ]}
        _, found = self.build([], household)
        by_uid = {e["UID"]: e for e in found}
        self.assertEqual(sorted(by_uid), ["shared-s1@pawmodoro", "shared-s2@pawmodoro", "shared-s5@pawmodoro"])
        self.assertEqual(by_uid["shared-s1@pawmodoro"]["DTSTART"], "20261013T193000")
        self.assertEqual(by_uid["shared-s2@pawmodoro"]["DTSTART"], "20261102T100000")
        self.assertEqual(by_uid["shared-s5@pawmodoro"]["RRULE"], "FREQ=MONTHLY;BYMONTHDAY=15")

    def test_long_lines_are_folded_without_splitting_characters(self):
        text, found = self.build([{"id": "l", "text": "🐾 " * 60, "recurrence": "monthly", "month_day": 3}])
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        self.assertEqual(found[0]["SUMMARY"], "🐾 " * 60)

    def test_nothing_to_export(self):
        _, count = calendar_export.build_ics([{"id": "e", "text": "Walk", "recurrence": "daily"}], None, TODAY)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
