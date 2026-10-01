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

    def test_repeating_reminder_clears_the_synced_time(self):
        task = self.storage.add_task("Water", "daily", reminder_time="09:00")
        self.storage.set_task_reminder(task["id"], None, every_hours=4)
        last = self.storage.data["sync_outbox"][-1]
        self.assertEqual(last["op"], "set_task_reminder")
        self.assertIsNone(last["args"]["reminder_time"])

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
        from supabase_sync import SyncError
        self.enable_sync()
        self.storage.add_note_page("Ideas")
        self.storage.add_task("Walk", "daily")
        engine = self.storage.sync

        class OldCloud:
            sent = []
            refresh_token = "t"

            def set_note_page(self, *a):
                raise SyncError("no such function", status=404, code="PGRST202")

            def add_task(self, *a):
                OldCloud.sent.append("add_task")

        engine.client = OldCloud()
        self.assertTrue(engine._flush())
        self.assertEqual(OldCloud.sent, ["add_task"])
        self.assertFalse(self.storage.data["notes_pages_synced"])
        self.assertEqual(self.storage.outbox_len(), 0)


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
