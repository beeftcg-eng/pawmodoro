"""Monthly tasks, due dates on one-off tasks, and a task's note and subtasks
(v2.18). The cloud half of these rules is in supabase/schema.sql."""
from datetime import date, datetime, timedelta

import gamification
import task_dates
from support import StorageTestCase, remote_state
from test_reminders import frozen_now


class TaskDatesTests(StorageTestCase):
    def test_month_occurrence(self):
        self.assertEqual(task_dates.month_occurrence(15, date(2026, 10, 20)), date(2026, 10, 15))
        self.assertEqual(task_dates.month_occurrence(15, date(2026, 10, 10)), date(2026, 9, 15))
        self.assertEqual(task_dates.month_occurrence(31, date(2026, 2, 28)), date(2026, 2, 28))
        self.assertEqual(task_dates.month_occurrence(31, date(2026, 3, 5)), date(2026, 2, 28))

    def test_normalize(self):
        self.assertEqual(task_dates.normalize("monthly", None, "2026-10-03"), (1, None))
        self.assertEqual(task_dates.normalize("once", 5, "2026-10-03"), (None, "2026-10-03"))
        self.assertEqual(task_dates.normalize("once", None, "not a date"), (None, None))
        self.assertEqual(task_dates.normalize("daily", 5, "2026-10-03"), (None, None))

    def test_due_status(self):
        today = date(2026, 10, 1)
        task = {"recurrence": "once", "due_date": "2026-09-30"}
        self.assertEqual(task_dates.due_status(task, today), "overdue")
        self.assertEqual(task_dates.due_status(dict(task, due_date="2026-10-01"), today), "today")
        self.assertIsNone(task_dates.due_status(dict(task, due_date="2026-10-02"), today))
        self.assertIsNone(task_dates.due_status(dict(task, completed_today=True), today))

    def test_describe(self):
        today = date(2026, 10, 1)
        self.assertEqual(task_dates.describe({"recurrence": "monthly", "month_day": 22}, today), "monthly, 22nd")
        self.assertEqual(task_dates.describe({"recurrence": "once", "due_date": "2026-10-01"}, today), "due today")
        self.assertEqual(task_dates.describe({"recurrence": "once", "due_date": "2026-10-03"}, today), "due Sat 03 Oct")
        self.assertEqual(task_dates.describe({"recurrence": "once"}, today), "once")


class MonthlyTaskTests(StorageTestCase):
    def test_pays_once_a_month(self):
        task = self.storage.add_task("Rent", "monthly", month_day=date.today().day)
        self.assertEqual(self.storage.set_task_done(task["id"], True)["xp_gained"], 20)
        self.storage.set_task_done(task["id"], False)
        self.assertNotIn("xp_gained", self.storage.set_task_done(task["id"], True))

    def test_award_rule(self):
        today = date(2026, 10, 20)
        self.assertTrue(gamification.task_already_awarded("monthly", "2026-10-15", today, 15))
        self.assertFalse(gamification.task_already_awarded("monthly", "2026-10-14", today, 15))
        self.assertTrue(gamification.task_already_awarded("monthly", "2026-09-20", date(2026, 10, 10), 15))

    def test_resets_when_its_day_comes_round_and_unticks_subtasks(self):
        st = self.storage
        task = st.add_task("Rent", "monthly", month_day=date.today().day)
        sub = st.add_subtask(task["id"], "Transfer")
        st.set_subtask_done(task["id"], sub["id"], True)
        st.set_task_done(task["id"], True)
        st._roll_recurring_tasks()
        self.assertTrue(task["completed_today"], "done this month: stays ticked")
        task["last_completed"] = (date.today() - timedelta(days=40)).isoformat()
        st._roll_recurring_tasks()
        self.assertFalse(task["completed_today"])
        self.assertFalse(task["subtasks"][0]["done"])

    def test_reminds_only_on_its_day(self):
        task = self.storage.add_task("Rent", "monthly", month_day=15)
        self.storage.set_task_reminder(task["id"], "09:00")
        with frozen_now(datetime(2026, 10, 14, 12)):
            self.assertEqual(self.storage.check_due_reminders(), [])
        with frozen_now(datetime(2026, 10, 15, 12)):
            self.assertEqual(len(self.storage.check_due_reminders()), 1)


class DueDateTests(StorageTestCase):
    def test_reminder_waits_for_the_due_date(self):
        task = self.storage.add_task("Taxes", "once", due_date="2026-10-03")
        self.storage.set_task_reminder(task["id"], "09:00")
        with frozen_now(datetime(2026, 10, 2, 12)):
            self.assertEqual(self.storage.check_due_reminders(), [])
        with frozen_now(datetime(2026, 10, 3, 12)):
            self.assertEqual(len(self.storage.check_due_reminders()), 1)
        with frozen_now(datetime(2026, 10, 4, 12)):
            self.assertEqual(len(self.storage.check_due_reminders()), 1, "still nags once it's overdue")

    def test_every_n_hours_also_waits(self):
        task = self.storage.add_task("Taxes", "once", due_date="2026-10-03")
        self.storage.set_task_reminder(task["id"], None, every_hours=1)
        with frozen_now(datetime(2026, 10, 2, 23)):
            self.assertEqual(self.storage.check_due_reminders(), [])

    def test_only_one_offs_take_a_due_date(self):
        daily = self.storage.add_task("Walk", "daily", due_date="2026-10-03")
        self.assertIsNone(daily["due_date"])
        self.storage.set_task_due_date(daily["id"], "2026-10-03")
        self.assertIsNone(daily["due_date"])
        once = self.storage.add_task("Taxes", "once")
        self.storage.set_task_due_date(once["id"], "2026-10-03")
        self.assertEqual(once["due_date"], "2026-10-03")
        self.storage.set_task_due_date(once["id"], None)
        self.assertIsNone(once["due_date"])


class SubtaskTests(StorageTestCase):
    def test_add_tick_rename_move_remove(self):
        st = self.storage
        task = st.add_task("Taxes", "once")
        a = st.add_subtask(task["id"], "Find receipts")
        b = st.add_subtask(task["id"], "Fill form")
        st.set_subtask_done(task["id"], a["id"], True)
        st.rename_subtask(task["id"], b["id"], "Fill the form")
        st.move_subtask(task["id"], b["id"], -1)
        self.assertEqual([(s["text"], s["done"]) for s in task["subtasks"]],
                         [("Fill the form", False), ("Find receipts", True)])
        st.move_subtask(task["id"], b["id"], -1)  # already first: no-op
        st.remove_subtask(task["id"], a["id"])
        self.assertEqual([s["text"] for s in task["subtasks"]], ["Fill the form"])
        st.set_task_note(task["id"], "Deadline is the 15th")
        self.assertEqual(self.new_storage().get_checklist()[0]["note"], "Deadline is the 15th")


class DetailSyncTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.enable_sync()

    def test_add_task_sends_the_month_day_and_due_date(self):
        self.storage.add_task("Rent", "monthly", month_day=31)
        self.storage.add_task("Taxes", "once", due_date="2026-10-03")
        adds = [o["args"] for o in self.storage.data["sync_outbox"] if o["op"] == "add_task"]
        self.assertEqual([(a["month_day"], a["due_date"]) for a in adds], [(31, None), (None, "2026-10-03")])

    def test_detail_edits_collapse_and_ticks_go_on_their_own(self):
        st = self.storage
        task = st.add_task("Taxes", "once")
        a = st.add_subtask(task["id"], "Find receipts")
        st.add_subtask(task["id"], "Fill form")
        st.set_task_note(task["id"], "note")
        st.set_subtask_done(task["id"], a["id"], True)
        details = [o for o in st.data["sync_outbox"] if o["op"] == "set_task_details"]
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["args"]["note"], "note")
        self.assertEqual(details[0]["args"]["subtasks"],
                         [{"id": s["id"], "text": s["text"]} for s in task["subtasks"]])
        self.assertEqual(st.data["sync_outbox"][-1]["op"], "set_subtask_done")
        self.assertEqual(st.data["sync_outbox"][-1]["args"], {"id": task["id"], "sub_id": a["id"], "done": True})

    def test_specific_day_task_details_stay_local(self):
        task = self.storage.add_task("Trash", "weekday", weekday=1)
        sub = self.storage.add_subtask(task["id"], "Recycling too")
        self.storage.set_subtask_done(task["id"], sub["id"], True)
        self.storage.set_task_note(task["id"], "blue bin")
        self.assertEqual(self.outbox_ops(), [])

    def test_undoing_a_removal_brings_the_details_back(self):
        st = self.storage
        task = st.add_task("Taxes", "once")
        st.add_subtask(task["id"], "Find receipts")
        st.restore_task(*st.remove_task(task["id"]))
        self.assertEqual(self.outbox_ops()[-2:], ["add_task", "set_task_details"])

    def test_pull_brings_the_new_fields(self):
        st = self.storage
        st.data["sync_outbox"] = []
        remote = remote_state([{"id": "r1", "text": "Rent", "recurrence": "monthly", "month_day": 1, "due_date": None,
                                "note": "landlord", "subtasks": [{"id": "s", "text": "Pay", "done": True}, "junk"]}])
        self.assertTrue(st.adopt_remote_state(remote))
        task = st.get_checklist()[0]
        self.assertEqual((task["month_day"], task["note"]), (1, "landlord"))
        self.assertEqual(task["subtasks"], [{"id": "s", "text": "Pay", "done": True}])

    def test_older_cloud_keeps_local_details(self):
        st = self.storage
        task = st.add_task("Taxes", "once", due_date="2026-10-03")
        st.add_subtask(task["id"], "Find receipts")
        st.set_task_note(task["id"], "note")
        st.data["sync_outbox"] = []
        st.adopt_remote_state(remote_state([{"id": task["id"], "text": "Taxes", "recurrence": "once"}]))
        task = st.get_checklist()[0]
        self.assertEqual((task["due_date"], task["note"], len(task["subtasks"])), ("2026-10-03", "note", 1))
