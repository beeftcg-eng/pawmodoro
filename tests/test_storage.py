"""Persistence, notes pages, focus tracking, the timer across restarts."""
import json
import os

import storage
from support import StorageTestCase


class PersistenceTests(StorageTestCase):
    def test_round_trip(self):
        self.storage.add_task("Walk", "daily")
        self.assertEqual([t["text"] for t in self.new_storage().get_checklist()], ["Walk"])

    def test_old_files_pick_up_new_defaults(self):
        with open(storage.DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"notes": "old", "reminders": {"quiet_start": "23:00"}}, f)
        st = self.new_storage()
        self.assertEqual(st.get_notes(), "old")
        self.assertEqual(st.get_reminder_settings()["quiet_start"], "23:00")
        self.assertEqual(st.get_reminder_settings()["quiet_end"], "08:00")
        self.assertEqual(st.data["notes_pages"], [])

    def test_corrupt_file_restored_from_backup(self):
        self.storage.set_notes("precious")
        os.makedirs(storage.BACKUP_DIR, exist_ok=True)
        self.storage._backup_daily()
        with open(storage.DATA_FILE, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(self.new_storage().get_notes(), "precious")


class NotesPagesTests(StorageTestCase):
    def test_main_page_is_the_synced_notes(self):
        self.storage.set_page_html("main", "<p>main</p>")
        self.assertEqual(self.storage.get_notes(), "<p>main</p>")
        self.assertEqual(self.storage.get_note_pages()[0]["id"], "main")

    def test_add_rename_move_remove_restore(self):
        st = self.storage
        ideas = st.add_note_page("Ideas")
        shop = st.add_note_page("Shopping")
        st.rename_note_page(shop, "Groceries")
        st.move_note_page(shop, 0)
        self.assertEqual([p["title"] for p in st.get_note_pages()], ["Notes", "Groceries", "Ideas"])
        st.set_current_note_page(ideas)
        removed = st.remove_note_page(ideas)
        self.assertEqual(st.get_current_note_page(), "main")
        st.restore_note_page(*removed)
        self.assertEqual([p["title"] for p in st.get_note_pages()], ["Notes", "Groceries", "Ideas"])

    def test_main_page_cannot_be_removed(self):
        self.assertIsNone(self.storage.remove_note_page("main"))

    def test_current_page_falls_back_when_it_is_gone(self):
        self.storage.data["notes_current_page"] = "deleted"
        self.assertEqual(self.storage.get_current_note_page(), "main")


class FocusTests(StorageTestCase):
    def test_sessions_count_on_the_chosen_task(self):
        task = self.storage.add_task("Report", "once")
        self.storage.set_focus_task(task["id"])
        self.storage.record_pomodoro_completed(25, task_id=task["id"])
        self.storage.record_pomodoro_completed(30, task_id=task["id"])
        self.assertEqual((task["focus_pomodoros"], task["focus_min"]), (2, 55))
        self.assertEqual(self.storage.get_focus_task()["id"], task["id"])

    def test_random_pick_only_takes_unfinished_one_time_tasks(self):
        st = self.storage
        self.assertIsNone(st.pick_random_focus_task())
        st.add_task("Walk", "daily")
        done = st.add_task("Old", "once")
        st.set_task_done(done["id"], True)
        st.add_task("Wish", "once", source="wishlist")
        a = st.add_task("Taxes", "once")
        b = st.add_task("Email", "once")
        self.assertEqual({t["id"] for t in st.pending_once_tasks()}, {a["id"], b["id"]})
        for _ in range(10):
            before = st.data["focus_task"]
            picked = st.pick_random_focus_task()
            self.assertIn(picked["id"], (a["id"], b["id"]))
            self.assertNotEqual(picked["id"], before)  # always a different one while there's a choice
            self.assertEqual(st.get_focus_task()["id"], picked["id"])
        st.set_task_done(a["id"], True)
        st.set_focus_task(b["id"])
        self.assertEqual(st.pick_random_focus_task()["id"], b["id"])  # the only one left

    def test_focus_auto_pick_setting(self):
        self.assertTrue(self.storage.get_focus_auto_pick())
        self.storage.set_focus_auto_pick(False)
        self.assertFalse(self.new_storage().get_focus_auto_pick())

    def test_focus_cleared_when_the_task_is_removed(self):
        task = self.storage.add_task("Report", "once")
        self.storage.set_focus_task(task["id"])
        self.storage.remove_task(task["id"])
        self.assertIsNone(self.storage.get_focus_task())

    def test_week_focus_by_task(self):
        a = self.storage.add_task("A", "daily")
        b = self.storage.add_task("B", "daily")
        self.storage.record_pomodoro_completed(25, task_id=a["id"])
        self.storage.record_pomodoro_completed(25, task_id=b["id"])
        self.storage.record_pomodoro_completed(25, task_id=b["id"])
        top = self.storage.get_week_focus_by_task()
        self.assertEqual([(row["text"], row["pomodoros"], row["focus_min"]) for row in top],
                         [("B", 2, 50), ("A", 1, 25)])


class BackupTests(StorageTestCase):
    def test_restore_keeps_logins_and_can_be_undone(self):
        st = self.storage
        st.set_notes("old")
        st._backup_daily()
        backup = st.list_backups()[0][0]
        st.set_notes("new")
        st.data["sync"]["refresh_token"] = "current-login"
        st.data["sync_outbox"] = [{"seq": 1, "op": "set_notes", "args": {"text": "new"}, "key": "notes"}]
        st.restore_from_file(backup)
        self.assertEqual(st.get_notes(), "old")
        self.assertEqual(st.data["sync"]["refresh_token"], "current-login")
        self.assertEqual(st.data["sync_outbox"], [])
        undo = [p for p, _ in st.list_backups() if p.endswith("data-before-restore.json")]
        self.assertEqual(len(undo), 1)
        st.restore_from_file(undo[0])
        self.assertEqual(st.get_notes(), "new")

    def test_restore_refuses_other_files(self):
        path = os.path.join(self.tmp, "other.json")
        with open(path, "w") as f:
            json.dump({"hello": 1}, f)
        with self.assertRaises(ValueError):
            self.storage.restore_from_file(path)
        self.assertEqual(self.storage.get_checklist(), [])

    def test_export_has_no_login_tokens(self):
        self.storage.data["sync"]["refresh_token"] = "secret"
        self.storage.data["spotify"]["refresh_token"] = "secret2"
        exported = json.dumps(self.storage.export_data())
        self.assertNotIn("secret", exported)


class TimerStateTests(StorageTestCase):
    def test_round_trip(self):
        state = {"phase": "work", "sessions_completed": 2, "seconds_left": 600,
                 "running": False, "ends_at": None, "day": "2026-10-01"}
        self.storage.set_timer_state(state)
        self.assertEqual(self.new_storage().get_timer_state(), state)
