"""The Calendar tab's logic: what's on a day (calendar_view), events and
attached files in Storage, how pulls replace them, and how the sync engine
uploads and deletes files."""
import os
import unittest
from datetime import date, datetime, timedelta, timezone

from support import StorageTestCase, remote_state

import calendar_export
import calendar_view
import storage
from supabase_sync import SyncError


def household(**extra):
    return {"id": "h1", "invite_code": "ABC", "members": [{"user_id": "u1", "name": "Ann", "is_me": True}],
            "tasks": [], "events": [], "attachments": [], **extra}


class CalendarViewTests(unittest.TestCase):
    def test_what_lands_on_a_day(self):
        day = date(2026, 10, 14)  # a Wednesday
        tasks = [
            {"id": "a", "text": "Passport", "recurrence": "once", "due_date": "2026-10-14", "reminder_time": "10:00"},
            {"id": "b", "text": "Rent", "recurrence": "monthly", "month_day": 14},
            {"id": "c", "text": "Bins", "recurrence": "weekday", "weekday": 2, "done_days": ["2026-10-14"]},
            {"id": "d", "text": "Water", "recurrence": "daily", "reminder_time": "09:00"},
            {"id": "e", "text": "Card", "recurrence": "once", "due_date": "2026-10-14", "source": "wishlist"},
        ]
        home = household(tasks=[
            {"id": "s1", "text": "Keys", "recurrence": "once", "due_date": "2026-10-14", "due_time": "08:00", "done": True},
            {"id": "s2", "text": "Hoover", "recurrence": "weekly", "weekday": 2, "done": False},
            {"id": "s3", "text": "Milk", "recurrence": "daily", "done": False},
        ], events=[{"id": "ev2", "title": "Dinner", "start_date": "2026-10-14", "end_date": "2026-10-14",
                    "start_time": "19:00"}])
        events = [{"id": "ev1", "title": "Lisbon", "start_date": "2026-10-12", "end_date": "2026-10-16"}]
        entries = calendar_view.entries_on(day, tasks, home, events, today=day)
        self.assertEqual([e["id"] for e in entries], ["ev1", "s2", "c", "b", "s1", "a", "ev2"])
        by_id = {e["id"]: e for e in entries}
        self.assertEqual(by_id["ev1"]["span"], "middle")
        self.assertTrue(by_id["c"]["done"])
        self.assertTrue(by_id["s1"]["done"])
        self.assertTrue(by_id["ev2"]["shared"])
        self.assertEqual(by_id["a"]["time"], "10:00")

    def test_an_event_over_several_days(self):
        event = {"id": "e", "title": "Trip", "start_date": "2026-10-20", "end_date": "2026-10-22", "start_time": "08:30"}
        spans = [calendar_view.entries_on(date(2026, 10, d), [], None, [event])[0] for d in (20, 21, 22)]
        self.assertEqual([(e["span"], e["time"]) for e in spans], [("start", "08:30"), ("middle", None), ("end", None)])
        self.assertEqual(calendar_view.entries_on(date(2026, 10, 23), [], None, [event]), [])

    def test_a_repeating_shared_item_is_only_done_on_its_current_day(self):
        task = {"recurrence": "weekly", "weekday": 0, "done": True}
        today = date(2026, 10, 14)
        self.assertTrue(calendar_view.shared_done_on(task, date(2026, 10, 12), today))
        self.assertFalse(calendar_view.shared_done_on(task, date(2026, 10, 19), today))

    def test_month_entries_and_grid(self):
        events = [{"id": "e", "title": "Trip", "start_date": "2026-09-29", "end_date": "2026-10-02"}]
        month = calendar_view.month_entries(2026, 10, [], None, events)
        self.assertEqual(sorted(month), [date(2026, 10, 1), date(2026, 10, 2)])
        rows = calendar_view.week_rows(2026, 10)
        self.assertEqual(rows[0][0], date(2026, 9, 28))
        self.assertEqual(rows[-1][-1], date(2026, 11, 1))

    def test_describe(self):
        today = date(2026, 10, 1)
        self.assertEqual(calendar_view.describe_event(
            {"start_date": "2026-10-20", "end_date": "2026-10-20", "start_time": "19:00", "end_time": "21:00"}, today),
            "Tue 20 Oct, 19:00–21:00")
        self.assertEqual(calendar_view.describe_event(
            {"start_date": "2026-10-20", "end_date": "2027-01-02"}, today), "Tue 20 Oct – Sat 02 Jan 2027")
        self.assertEqual(calendar_view.human_size(2_500_000), "2.4 MB")


class CalendarTestCase(StorageTestCase):
    def make_file(self, name, size=10):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(b"x" * size)
        return path


class EventStorageTests(CalendarTestCase):
    def test_events_are_saved_and_queued(self):
        self.enable_sync()
        event = self.storage.save_event("Dentist", "2026-10-14", None, "15:30", "16:00", "bring card")
        self.assertEqual(self.storage.get_events(), [event])
        self.assertEqual(event["end_date"], "2026-10-14")
        self.assertFalse(event["synced"])
        args = self.storage.data["sync_outbox"][-1]["args"]
        self.assertEqual((args["title"], args["start_time"], args["end_time"], args["shared"]),
                         ("Dentist", "15:30", "16:00", False))
        self.storage.save_event("Dentist", "2026-10-15", None, None, "16:00", event_id=event["id"])
        changed = self.storage.get_events()[0]
        self.assertEqual((changed["start_date"], changed["end_time"]), ("2026-10-15", None), "all day: no end time")
        self.assertEqual(self.outbox_ops(), ["event_save"], "edits collapse")
        self.storage.remove_event(event["id"])
        self.assertEqual(self.storage.get_events(), [])
        self.assertEqual(self.outbox_ops(), ["event_remove"])

    def test_bad_events_are_refused(self):
        with self.assertRaises(ValueError):
            self.storage.save_event("Backwards", "2026-10-14", "2026-10-13")
        with self.assertRaises(ValueError):
            self.storage.save_event("Odd", "2026-10-14", None, "10:00", "09:00")
        with self.assertRaises(ValueError):
            self.storage.save_event("  ", "2026-10-14")

    def test_sharing_needs_a_household_and_moves_the_files(self):
        self.enable_sync()
        event = self.storage.save_event("Trip", "2026-10-20", "2026-10-25", shared=True)
        self.assertEqual(self.storage.find_event(event["id"]), (event, False), "no household: stays personal")
        attachment = self.storage.add_attachment("event", event["id"], self.make_file("ticket.pdf"))
        self.storage.data["household"] = household()
        self.storage.save_event("Trip", "2026-10-20", "2026-10-25", shared=True, event_id=event["id"])
        self.assertEqual(self.storage.get_events(), [])
        self.assertEqual(self.storage.get_shared_events()[0]["id"], event["id"])
        self.assertEqual(self.storage.get_household()["attachments"][0]["id"], attachment["id"])
        self.assertEqual(self.storage.data["attachments"], [])
        self.assertTrue(self.storage.data["sync_outbox"][-1]["args"]["shared"])


class AttachmentStorageTests(CalendarTestCase):
    def setUp(self):
        super().setUp()
        self.task = self.storage.add_task("Pay rent", "monthly", month_day=1)

    def test_a_file_is_copied_and_queued(self):
        self.enable_sync()
        attachment = self.storage.add_attachment("task", self.task["id"], self.make_file("receipt.pdf", 1234))
        self.assertEqual((attachment["file_name"], attachment["mime"], attachment["size"]),
                         ("receipt.pdf", "application/pdf", 1234))
        local = self.storage.attachment_local_path(attachment)
        self.assertTrue(local and local.startswith(storage.attachments_dir()))
        self.assertEqual(self.storage.attachments_for("task", self.task["id"]), [attachment])
        op = self.storage.data["sync_outbox"][-1]
        self.assertEqual((op["op"], op["args"]["item_kind"], op["args"]["household_id"]), ("attachment_add", "task", None))

    def test_too_big_or_shared_without_a_household(self):
        big = self.make_file("movie.mp4")
        os.truncate(big, storage.ATTACHMENT_MAX_BYTES + 1)
        with self.assertRaises(ValueError):
            self.storage.add_attachment("task", self.task["id"], big)
        with self.assertRaises(ValueError):
            self.storage.add_attachment("shared", "s1", self.make_file("a.txt"))

    def test_removing_a_waiting_upload_cancels_it(self):
        self.enable_sync()
        attachment = self.storage.add_attachment("task", self.task["id"], self.make_file("a.jpg"))
        self.storage.remove_attachment(attachment["id"])
        self.assertEqual(self.outbox_ops()[-1:], ["attachment_remove"])
        self.assertNotIn("attachment_add", self.outbox_ops())
        self.assertEqual(self.storage.attachments_for("task", self.task["id"]), [])
        self.assertFalse(os.path.exists(os.path.join(storage.attachments_dir(), attachment["id"])))

    def test_removing_an_event_drops_its_files_here(self):
        self.enable_sync()
        event = self.storage.save_event("Trip", "2026-10-20")
        attachment = self.storage.add_attachment("event", event["id"], self.make_file("hotel.pdf"))
        self.storage.remove_event(event["id"])
        self.assertIsNone(self.storage.find_attachment(attachment["id"]))
        self.assertNotIn("attachment_add", self.outbox_ops(), "the waiting upload went too")

    def test_files_of_removed_tasks_are_pruned_at_the_next_start(self):
        attachment = self.storage.add_attachment("task", self.task["id"], self.make_file("a.txt"))
        removed = self.storage.remove_task(self.task["id"])
        self.storage.restore_task(*removed)  # an undo keeps them
        self.assertEqual(len(self.new_storage().attachments_for("task", self.task["id"])), 1)
        self.storage.remove_task(self.task["id"])
        restarted = self.new_storage()
        self.assertIsNone(restarted.find_attachment(attachment["id"]))


class CalendarPullTests(CalendarTestCase):
    def pull(self, **extra):
        remote = remote_state([])
        remote.update(extra)
        self.assertTrue(self.storage.adopt_remote_state(remote))

    def test_cloud_events_replace_local_ones_but_unsent_ones_stay(self):
        self.storage.save_event("Made offline", "2026-10-14")  # sync off: never queued
        self.pull(events=[{"id": "c1", "title": "From the phone", "start_date": "2026-10-15",
                           "end_date": "2026-10-15", "start_time": None, "end_time": None, "note": ""}])
        titles = [e["title"] for e in self.storage.get_events()]
        self.assertEqual(titles, ["From the phone", "Made offline"])
        self.enable_sync()
        self.pull(events=[])
        self.assertEqual([e["title"] for e in self.storage.get_events()], ["Made offline"])
        self.assertEqual(self.outbox_ops(), ["event_save"], "sent once sync is on")

    def test_older_cloud_leaves_events_and_files_alone(self):
        self.storage.save_event("Here", "2026-10-14")
        self.pull()
        self.assertEqual(len(self.storage.get_events()), 1)

    def test_files_from_the_cloud_and_the_trash(self):
        task = self.storage.add_task("Rent", "monthly", month_day=1)
        local = self.storage.add_attachment("task", task["id"], self.make_file("mine.pdf"))
        trashed_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        cloud_file = {"id": "f1", "item_kind": "task", "item_id": task["id"], "file_name": "phone.jpg",
                      "mime": "image/jpeg", "size": 5, "path": "u/x/f1"}
        self.enable_sync()
        self.pull(checklist=[dict(task, done_days=[], subtasks=[])], attachments=[cloud_file],
                  attachments_trash=[{"id": "old", "path": "u/x/old", "trashed_at": trashed_at},
                                     {"id": "new", "path": "u/x/new", "trashed_at": datetime.now(timezone.utc).isoformat()}])
        names = [a["file_name"] for a in self.storage.attachments_for("task", task["id"])]
        self.assertEqual(names, ["phone.jpg", "mine.pdf"])
        self.assertTrue(self.storage.find_attachment("f1")["uploaded"])
        self.assertIsNone(self.storage.attachment_local_path(self.storage.find_attachment("f1")), "not downloaded yet")
        self.assertIn("attachment_add", self.outbox_ops(), "the one made offline is sent")
        self.assertEqual(self.storage.find_attachment(local["id"])["file_name"], "mine.pdf")
        self.storage.data["sync_outbox"] = []
        self.storage.purge_trashed_attachments()
        removes = [o["args"]["id"] for o in self.storage.data["sync_outbox"] if o["op"] == "attachment_remove"]
        self.assertEqual(removes, ["old"], "only what's been in the trash for an hour")
        self.assertEqual([t["id"] for t in self.storage.data["attachment_trash"]], ["new"])

    def test_a_refused_file_is_not_sent_again(self):
        task = self.storage.add_task("Rent", "monthly", month_day=1)
        attachment = self.storage.add_attachment("task", task["id"], self.make_file("huge.pdf"))
        self.storage.outbox_attachment_failed(attachment["id"], "out of space")
        self.enable_sync()
        self.pull(checklist=[dict(task, done_days=[], subtasks=[])], attachments=[])
        self.assertEqual(self.outbox_ops(), [])
        self.assertEqual(self.storage.find_attachment(attachment["id"])["upload_error"], "out of space")


class FileCloud:
    """SupabaseSync's calendar/file calls, recorded."""
    refresh_token = "t"
    user_id = "user-1"

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}

    def _call(self, name, *args):
        self.calls.append((name, args))
        if name in self.fail:
            raise self.fail[name]

    def add_attachment(self, attachment_id, kind, item_id, file_name, mime, size):
        self._call("add_attachment", attachment_id)
        return {"path": f"u/user-1/{attachment_id}"}

    def upload_file(self, path, data, mime):
        self._call("upload_file", path, data)

    def attachment_uploaded(self, attachment_id):
        self._call("attachment_uploaded", attachment_id)

    def delete_file(self, path):
        self._call("delete_file", path)

    def remove_attachment(self, attachment_id):
        self._call("remove_attachment", attachment_id)

    def save_event(self, event, shared):
        self._call("save_event", event["id"], shared)

    def remove_event(self, event_id):
        self._call("remove_event", event_id)


class EngineFileTests(CalendarTestCase):
    def setUp(self):
        super().setUp()
        self.enable_sync()
        self.task = self.storage.add_task("Pay rent", "monthly", month_day=1)
        self.storage.data["sync_outbox"] = []

    def flush(self, cloud):
        self.storage.sync.client = cloud
        return self.storage.sync._flush()

    def test_upload_registers_sends_and_marks_done(self):
        attachment = self.storage.add_attachment("task", self.task["id"], self.make_file("r.pdf", 3))
        cloud = FileCloud()
        self.assertTrue(self.flush(cloud))
        self.assertEqual([c[0] for c in cloud.calls], ["add_attachment", "upload_file", "attachment_uploaded"])
        self.assertEqual(cloud.calls[1][1], (f"u/user-1/{attachment['id']}", b"xxx"))
        done = self.storage.find_attachment(attachment["id"])
        self.assertTrue(done["uploaded"])
        self.assertEqual(done["path"], f"u/user-1/{attachment['id']}")

    def test_a_refused_upload_is_marked_and_dropped(self):
        attachment = self.storage.add_attachment("task", self.task["id"], self.make_file("r.pdf"))
        refused = SyncError('400: {"message": "your attachments are using all 250 MB"}', status=400)
        self.assertTrue(self.flush(FileCloud(fail={"add_attachment": refused})))
        self.assertEqual(self.storage.find_attachment(attachment["id"])["upload_error"],
                         "your attachments are using all 250 MB")

    def test_offline_keeps_it_queued(self):
        self.storage.add_attachment("task", self.task["id"], self.make_file("r.pdf"))
        self.assertFalse(self.flush(FileCloud(fail={"upload_file": SyncError("offline")})))
        self.assertEqual(self.outbox_ops(), ["attachment_add"])

    def test_a_refused_event_is_not_sent_again(self):
        event = self.storage.save_event("Trip", "2026-10-20")
        refused = SyncError('400: {"message": "an event can last at most a year"}', status=400)
        self.assertTrue(self.flush(FileCloud(fail={"save_event": refused})))
        self.assertEqual(self.storage.get_events()[0]["sync_error"], "an event can last at most a year")
        remote = remote_state([])
        remote["events"] = []
        self.assertTrue(self.storage.adopt_remote_state(remote))
        self.assertEqual(self.outbox_ops(), [], "kept here, not re-sent on every pull")
        self.storage.save_event("Trip", "2026-10-21", event_id=event["id"])
        self.assertNotIn("sync_error", self.storage.get_events()[0], "an edit tries again")

    def test_only_its_maker_takes_a_household_event_back(self):
        self.storage.data["household"] = household(events=[{
            "id": "ev9", "title": "Dinner", "start_date": "2026-10-18", "end_date": "2026-10-18",
            "created_by_me": False, "created_by_name": "Bo"}])
        with self.assertRaises(ValueError):
            self.storage.save_event("Dinner", "2026-10-18", shared=False, event_id="ev9")
        self.storage.save_event("Dinner at Mum's", "2026-10-18", shared=True, event_id="ev9")
        self.assertEqual(self.storage.get_shared_events()[0]["title"], "Dinner at Mum's")

    def test_an_older_cloud_keeps_it_queued(self):
        attachment = self.storage.add_attachment("task", self.task["id"], self.make_file("r.pdf"))
        self.storage.save_event("Trip", "2026-10-20")
        old = SyncError("no such function", status=404, code="PGRST202")
        self.assertFalse(self.flush(FileCloud(fail={"add_attachment": old, "save_event": old})))
        self.assertEqual(self.outbox_ops(), ["attachment_add", "event_save"])
        self.assertIsNone(self.storage.find_attachment(attachment["id"])["upload_error"])

    def test_removing_one_that_never_reported_its_path(self):
        attachment = self.storage.add_attachment("task", self.task["id"], self.make_file("r.pdf"))
        self.storage.data["sync_outbox"] = []  # as if its upload had gone through
        self.storage.remove_attachment(attachment["id"])
        cloud = FileCloud()
        self.assertTrue(self.flush(cloud))
        self.assertEqual(cloud.calls, [("delete_file", (f"u/user-1/{attachment['id']}",)),
                                       ("remove_attachment", (attachment["id"],))])


class ExportTests(unittest.TestCase):
    def test_events_go_into_the_ics(self):
        events = [{"id": "e1", "title": "Trip, Lisbon", "start_date": "2026-10-20", "end_date": "2026-10-25",
                   "note": "TP123"},
                  {"id": "e2", "title": "Dentist", "start_date": "2026-10-14", "end_date": "2026-10-14",
                   "start_time": "15:30", "end_time": None}]
        home = {"events": [{"id": "e3", "title": "Dinner", "start_date": "2026-10-18", "end_date": "2026-10-18",
                            "start_time": "19:00", "end_time": "21:00"}], "tasks": []}
        text, count = calendar_export.build_ics([], home, today=date(2026, 10, 1), events=events,
                                                now=datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(count, 3)
        self.assertIn("SUMMARY:Trip\\, Lisbon", text)
        self.assertIn("DTSTART;VALUE=DATE:20261020\r\nDTEND;VALUE=DATE:20261026", text)
        self.assertIn("DTSTART:20261014T153000\r\nDTEND:20261014T163000", text)
        self.assertIn("DTSTART:20261018T190000\r\nDTEND:20261018T210000", text)
        self.assertIn("CATEGORIES:Shared", text)


if __name__ == "__main__":
    unittest.main()
