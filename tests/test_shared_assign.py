"""Shared items given to one member: storage, reminders, notifications."""
from datetime import date

import shared_activity
from support import StorageTestCase

ME, BEA = "u-me", "u-bea"


def household(tasks):
    return {"id": "h1", "invite_code": "CODE",
            "members": [{"user_id": ME, "name": "Me", "is_me": True}, {"user_id": BEA, "name": "Bea", "is_me": False}],
            "tasks": tasks}


def item(task_id, **extra):
    return {"id": task_id, "text": task_id.title(), "done": False, "recurrence": "daily", "due_time": "00:00",
            "weekday": None, "month_day": None, "due_date": None, **extra}


class AssignTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.enable_sync()
        self.storage.data["household"] = household([item("bins"), item("dishes")])

    def task(self, task_id):
        return next(t for t in self.storage.get_household()["tasks"] if t["id"] == task_id)

    def test_assigning_is_local_first_and_queued(self):
        self.storage.shared_assign("bins", BEA)
        self.assertEqual((self.task("bins")["assigned_name"], self.task("bins")["assigned_to_me"]), ("Bea", False))
        self.storage.shared_assign("bins", ME)
        self.assertTrue(self.task("bins")["assigned_to_me"])
        self.storage.shared_assign("bins", None)
        self.assertIsNone(self.task("bins")["assigned_to"])
        self.assertEqual([o["args"] for o in self.storage.data["sync_outbox"] if o["op"] == "shared_assign"],
                         [{"id": "bins", "user_id": BEA}, {"id": "bins", "user_id": ME}, {"id": "bins", "user_id": None}])

    def test_someone_who_left_cannot_be_assigned(self):
        self.storage.shared_assign("bins", "u-gone")
        self.assertIsNone(self.task("bins")["assigned_to"])
        self.assertIsNone(self.storage.data["sync_outbox"][-1]["args"]["user_id"])

    def test_only_your_own_turns_remind_you(self):
        self.storage.shared_assign("bins", BEA)
        self.storage.shared_assign("dishes", ME)
        self.assertEqual([t["id"] for t in self.storage.check_due_shared_reminders()], ["dishes"])

    def test_label_says_whose_turn(self):
        from shared_tab import SharedTab
        self.assertIn("→ Bea", SharedTab._label_for(item("bins", assigned_to=BEA, assigned_name="Bea")))
        self.assertIn("→ you", SharedTab._label_for(item("bins", assigned_to=ME, assigned_to_me=True)))
        self.assertNotIn("→", SharedTab._label_for(item("bins")))


class ActivityTests(StorageTestCase):
    def test_your_turn_is_announced_once(self):
        before = [item("bins"), item("dishes", assigned_to=ME, assigned_to_me=True)]
        after = [item("bins", assigned_to=ME, assigned_to_me=True), item("dishes", assigned_to=ME, assigned_to_me=True)]
        changes = shared_activity.detect(before, after)
        self.assertEqual([e["text"] for e in changes["assigned"]], ["Bins"])
        self.assertIn(("Shared list: your turn", "Your turn: Bins"), shared_activity.notifications(changes))
        self.assertEqual(shared_activity.detect(after, after)["assigned"], [])

    def test_older_cloud_without_assignment_reports_nothing_new(self):
        changes = shared_activity.detect([item("bins")], [item("bins")])
        self.assertEqual(shared_activity.notifications(changes), [])
