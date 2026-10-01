"""The local side of cloud sync: what gets queued, and what a pull may
overwrite. (The server side is supabase/schema.sql.)"""
from support import StorageTestCase, remote_state


class OutboxTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.enable_sync()

    def test_nothing_queued_while_sync_is_off(self):
        self.storage.data["sync"]["enabled"] = False
        self.storage.add_task("Walk", "daily")
        self.assertEqual(self.outbox_ops(), [])

    def test_specific_day_tasks_never_reach_the_cloud(self):
        task = self.storage.add_task("Trash", "weekday", weekday=2)
        self.storage.rename_task(task["id"], "Bins")
        self.storage.set_task_reminder(task["id"], "07:00")
        self.storage.remove_task(task["id"])
        self.assertNotIn("add_task", self.outbox_ops())
        self.assertNotIn("set_task_reminder", self.outbox_ops())
        self.assertNotIn("remove_task", self.outbox_ops())

    def test_repeating_reminder_is_queued(self):
        task = self.storage.add_task("Water", "daily", reminder_time="09:00")
        self.storage.set_task_reminder(task["id"], None, every_hours=4)
        last = self.storage.data["sync_outbox"][-1]
        self.assertEqual(last["op"], "set_task_reminder_mode")
        self.assertEqual((last["args"]["reminder_time"], last["args"]["every_h"]), (None, 4))

    def test_snooze_and_quiet_hours_are_queued(self):
        task = self.storage.add_task("Water", "daily")
        self.storage.snooze_task_reminder(task["id"], 15)
        snooze = self.storage.data["sync_outbox"][-1]
        self.assertEqual(snooze["op"], "snooze_task")
        from datetime import datetime
        self.assertIsNotNone(datetime.fromisoformat(snooze["args"]["until"]).tzinfo,
                             "the cloud needs a timezone-aware time")
        self.storage.set_reminder_settings(quiet_enabled=True)
        self.storage.set_reminder_settings(quiet_start="23:00")
        settings = [o for o in self.storage.data["sync_outbox"] if o["op"] == "set_reminder_settings"]
        self.assertEqual(len(settings), 1)
        self.assertEqual(settings[0]["args"], {"quiet_enabled": True, "quiet_start": "23:00", "quiet_end": "08:00"})

    def test_notes_edits_collapse_to_the_latest(self):
        for text in ("a", "ab", "abc"):
            self.storage.set_notes(text)
        notes = [o for o in self.storage.data["sync_outbox"] if o["op"] == "set_notes"]
        self.assertEqual([o["args"]["text"] for o in notes], ["abc"])

    def test_notes_page_edits_collapse_per_page(self):
        page = self.storage.add_note_page("Ideas")
        for text in ("<p>a</p>", "<p>ab</p>"):
            self.storage.set_page_html(page, text)
        sets = [o for o in self.storage.data["sync_outbox"] if o["op"] == "note_page_set"]
        self.assertEqual([o["args"]["html"] for o in sets], ["<p>ab</p>"])
        self.storage.remove_note_page(page)
        self.assertEqual([o["op"] for o in self.storage.data["sync_outbox"] if o.get("key") == f"page:{page}"],
                         ["note_page_remove"])

    def test_pages_made_before_sync_are_uploaded_once(self):
        st = self.storage
        st.data["sync"]["enabled"] = False
        page = st.add_note_page("Old page")
        self.assertEqual(self.outbox_ops(), [])
        st.save_sync_config("https://example.invalid", "k", "me@example.com", "t", True)
        self.assertIn("note_page_set", self.outbox_ops())
        self.assertIn("note_pages_reorder", self.outbox_ops())
        self.assertTrue(st.data["notes_pages_synced"])
        reopened = self.new_storage()
        uploads = [o for o in reopened.data["sync_outbox"] if o["op"] == "note_page_set"]
        self.assertEqual([o["args"]["id"] for o in uploads], [page], "queued twice")


class PagesPullTests(StorageTestCase):
    def remote_with_pages(self, pages):
        remote = remote_state([])
        remote["notes_pages"] = pages
        return remote

    def test_cloud_pages_replace_local_ones_once_uploaded(self):
        self.storage.add_note_page("Local")
        self.storage.data["notes_pages_synced"] = True
        self.storage._apply_remote_state(self.remote_with_pages([{"id": "c1", "title": "From phone", "html": "x"}]))
        self.assertEqual([p["title"] for p in self.storage.get_note_pages()], ["Notes", "From phone"])

    def test_pull_never_deletes_pages_that_were_not_uploaded(self):
        self.enable_sync()
        self.storage.data["notes_pages_synced"] = False
        self.storage.add_note_page("Only here")
        self.storage._apply_remote_state(self.remote_with_pages([]))
        self.assertEqual([p["title"] for p in self.storage.get_note_pages()], ["Notes", "Only here"])
        self.assertTrue(self.storage.data["notes_pages_synced"], "should have queued them for upload")

    def test_older_cloud_without_pages_leaves_them_alone(self):
        self.storage.add_note_page("Kept")
        self.storage.data["notes_pages_synced"] = True
        self.storage._apply_remote_state(remote_state([]))  # no "notes_pages" key at all
        self.assertEqual(len(self.storage.get_note_pages()), 2)

    def test_page_being_typed_in_is_kept(self):
        page = self.storage.add_note_page("Typing")
        self.storage.set_page_html(page, "<p>mine</p>")
        self.storage.set_current_note_page(page)
        self.storage.data["notes_pages_synced"] = True
        remote = self.remote_with_pages([{"id": page, "title": "Typing", "html": "<p>theirs</p>"}])
        self.storage._apply_remote_state(remote, keep_notes=True)
        self.assertEqual(self.storage.get_page_html(page), "<p>mine</p>")

    def test_old_cloud_schema_drops_page_uploads_instead_of_blocking(self):
        self.enable_sync()
        self.storage.add_note_page("Ideas")
        self.storage.add_task("Walk", "daily")
        cloud = FakeCloud(set_note_page_checked=outdated(), set_note_page=outdated())
        self.storage.sync.client = cloud
        self.assertTrue(self.storage.sync._flush())
        self.assertIn("add_task", [c[0] for c in cloud.calls])
        self.assertFalse(self.storage.data["notes_pages_synced"])
        self.assertEqual(self.storage.outbox_len(), 0)


class FakeCloud:
    """Stands in for SupabaseSync in SyncEngine tests: records calls, and
    raises `errors[name]` for the named RPC if given."""
    refresh_token = "t"

    def __init__(self, **errors):
        self.calls = []
        self.errors = errors
        self.notes = {"rev": 5, "text": "the phone's notes"}

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            if name in self.errors:
                raise self.errors[name]
            if name == "set_notes_checked":
                text, base = args
                if base is not None and base != self.notes["rev"] and text != self.notes["text"]:
                    return {"ok": False, "rev": self.notes["rev"], "notes": self.notes["text"]}
                self.notes = {"rev": self.notes["rev"] + 1, "text": text}
                return {"ok": True, "rev": self.notes["rev"]}
            return None
        return call


def outdated():
    from supabase_sync import SyncError
    return SyncError("no such function", status=404, code="PGRST202")


class ConflictTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.enable_sync()

    def flush(self, cloud):
        self.storage.sync.client = cloud
        self.assertTrue(self.storage.sync._flush())

    def test_up_to_date_save_goes_straight_through(self):
        cloud = FakeCloud()
        self.storage.data["notes_rev"] = 5
        self.storage.set_notes("mine")
        self.flush(cloud)
        self.assertEqual(cloud.notes, {"rev": 6, "text": "mine"})
        self.assertEqual(self.storage.data["notes_rev"], 6)
        self.assertEqual(self.storage.take_notes_conflicts(), [])

    def test_conflict_keeps_both_versions(self):
        cloud = FakeCloud()
        self.storage.data["notes_rev"] = 3  # the phone saved twice since
        self.storage.set_notes("written offline here")
        self.flush(cloud)
        self.assertEqual(cloud.notes["text"], "written offline here")
        made = self.storage.take_notes_conflicts()
        self.assertEqual(len(made), 1)
        page = next(p for p in self.storage.get_note_pages() if p["title"] == made[0])
        self.assertEqual(self.storage.get_page_html(page["id"]), "the phone's notes")
        self.assertEqual(self.storage.get_notes(), "written offline here")

    def test_a_second_edit_after_our_own_upload_is_not_a_conflict(self):
        cloud = FakeCloud()
        self.storage.data["notes_rev"] = 5
        self.storage.set_notes("first")
        self.flush(cloud)
        self.storage.set_notes("second")
        self.flush(cloud)
        self.assertEqual(self.storage.take_notes_conflicts(), [])
        self.assertEqual(cloud.notes["text"], "second")

    def test_typing_during_a_pull_keeps_the_old_revision(self):
        self.storage.data["notes_rev"] = 2
        self.storage._apply_remote_state(dict(remote_state([], notes="theirs"), notes_rev=4), keep_notes=True)
        self.assertEqual(self.storage.data["notes_rev"], 2, "else the upload would overwrite theirs unchecked")
        self.storage._apply_remote_state(dict(remote_state([], notes="theirs"), notes_rev=4))
        self.assertEqual(self.storage.data["notes_rev"], 4)

    def test_older_cloud_gets_a_plain_save(self):
        cloud = FakeCloud(set_notes_checked=outdated())
        self.storage.set_notes("mine")
        self.flush(cloud)
        self.assertEqual([c[0] for c in cloud.calls], ["set_notes_checked", "set_notes"])

    def test_older_cloud_and_new_reminder_ops(self):
        task = self.storage.add_task("Water", "daily")
        self.storage.set_task_reminder(task["id"], None, every_hours=3)
        self.storage.snooze_task_reminder(task["id"], 15)
        self.storage.set_reminder_settings(quiet_enabled=True)
        cloud = FakeCloud(set_task_reminder_mode=outdated(), snooze_task=outdated(), set_reminder_settings=outdated())
        self.flush(cloud)
        self.assertIn(("set_task_reminder", (task["id"], None)), cloud.calls)
        self.assertEqual(self.storage.outbox_len(), 0, "nothing may wedge the queue")


class ReminderPullTests(StorageTestCase):
    def test_every_n_hours_and_snooze_come_from_the_cloud(self):
        task = self.storage.add_task("Meds", "daily")
        remote = [{"id": task["id"], "text": "Meds", "recurrence": "daily", "reminder_time": None,
                   "reminder_every_h": 6, "snoozed_until": "2099-01-01T12:00:00+00:00"}]
        self.storage._apply_remote_state(remote_state(remote))
        pulled = self.storage._find_task(task["id"])
        self.assertEqual(pulled["reminder_every_h"], 6)
        self.assertTrue(pulled["last_reminded_at"], "a repeating reminder set elsewhere starts counting now")
        self.assertTrue(pulled["snoozed_until"].startswith("2099-01-01"))
        self.assertNotIn("+", pulled["snoozed_until"], "stored as local time")

    def test_a_snooze_that_already_fired_here_does_not_fire_again(self):
        from datetime import datetime, timedelta
        task = self.storage.add_task("Meds", "daily")
        self.storage.snooze_task_reminder(task["id"], 0)
        task["snoozed_until"] = (datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds")
        fired_value = task["snoozed_until"]
        self.assertEqual(len(self.storage.check_due_reminders()), 1)
        cloud_copy = datetime.fromisoformat(fired_value).astimezone().isoformat()
        remote = [{"id": task["id"], "text": "Meds", "recurrence": "daily", "reminder_time": None,
                   "reminder_every_h": None, "snoozed_until": cloud_copy}]
        self.storage._apply_remote_state(remote_state(remote))
        self.assertIsNone(self.storage._find_task(task["id"])["snoozed_until"])
        self.assertEqual(self.storage.check_due_reminders(), [])

    def test_quiet_hours_come_from_the_cloud(self):
        remote = remote_state([])
        remote["reminders"] = {"quiet_enabled": True, "quiet_start": "21:00", "quiet_end": "06:30"}
        self.storage._apply_remote_state(remote)
        self.assertEqual(self.storage.get_reminder_settings(),
                         {"quiet_enabled": True, "quiet_start": "21:00", "quiet_end": "06:30"})


class PullTests(StorageTestCase):
    def test_stale_pull_is_refused(self):
        rev = self.storage.rev
        self.storage.add_task("Made after the pull began", "daily")
        self.assertFalse(self.storage.adopt_remote_state(remote_state([]), rev=rev))
        self.assertEqual(len(self.storage.get_checklist()), 1)

    def test_pull_refused_while_changes_wait_to_upload(self):
        self.enable_sync()
        self.storage.add_task("Waiting", "daily")
        self.assertFalse(self.storage.adopt_remote_state(remote_state([]), rev=self.storage.rev))

    def test_fresh_pull_replaces_the_checklist(self):
        rev = self.storage.rev
        remote = [{"id": "r1", "text": "From the phone", "recurrence": "daily"}]
        self.assertTrue(self.storage.adopt_remote_state(remote_state(remote, notes="phone"), rev=rev))
        self.assertEqual([t["text"] for t in self.storage.get_checklist()], ["From the phone"])
        self.assertEqual(self.storage.get_notes(), "phone")

    def test_local_only_fields_survive_a_pull(self):
        task = self.storage.add_task("Meds", "daily")
        self.storage.set_task_reminder(task["id"], None, every_hours=8)
        self.storage.snooze_task_reminder(task["id"], 30)
        self.storage.record_pomodoro_completed(25, task_id=task["id"])
        weekday_task = self.storage.add_task("Trash", "weekday", weekday=1)
        page = self.storage.add_note_page("Ideas")
        self.storage.set_page_html(page, "<p>kept</p>")

        remote = [{"id": task["id"], "text": "Meds", "recurrence": "daily", "reminder_time": None}]
        self.storage._apply_remote_state(remote_state(remote))

        pulled = self.storage._find_task(task["id"])
        self.assertEqual(pulled["reminder_every_h"], 8)
        self.assertIsNotNone(pulled["snoozed_until"])
        self.assertEqual(pulled["focus_pomodoros"], 1)
        self.assertEqual(pulled["focus_min"], 25)
        self.assertIsNotNone(self.storage._find_task(weekday_task["id"]), "specific-day task was deleted")
        self.assertEqual(self.storage.get_page_html(page), "<p>kept</p>")

    def test_a_time_set_on_the_phone_replaces_a_repeating_reminder(self):
        task = self.storage.add_task("Meds", "daily")
        self.storage.set_task_reminder(task["id"], None, every_hours=8)
        remote = [{"id": task["id"], "text": "Meds", "recurrence": "daily", "reminder_time": "09:00"}]
        self.storage._apply_remote_state(remote_state(remote))
        self.assertIsNone(self.storage._find_task(task["id"])["reminder_every_h"])
