"""Headless smoke tests of the real window (skipped without PyQt6)."""
import time
import unittest
from unittest import mock

from support import StorageTestCase

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
except ImportError:  # e.g. a CI job that only runs the logic tests
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 isn't installed")
class MainWindowTests(StorageTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def make_window(self):
        import main
        with mock.patch.object(main, "Storage", self.new_storage):
            window = main.MainWindow()
        self.addCleanup(window.storage.sync.stop)
        self.addCleanup(window.widget_window.close)
        self.addCleanup(window.deleteLater)
        return window

    def test_builds_and_switches_every_tab(self):
        window = self.make_window()
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            self.app.processEvents()

    def test_find_carries_on_into_other_pages(self):
        st = self.storage
        st.set_page_html("main", "<p>groceries</p>")
        ideas = st.add_note_page("Ideas")
        st.set_page_html(ideas, "<p>a Bakery list</p>")
        st.add_note_page("Empty")
        window = self.make_window()
        notes = window.notes_checklist_tab.notes_tab
        notes.find_input.setText("bakery")
        notes.find_next()
        self.assertEqual(notes.current_page, ideas)
        self.assertEqual(notes.editor.textCursor().selectedText(), "Bakery")
        notes.find_input.setText("groceries")
        notes.find_previous()
        self.assertEqual(notes.current_page, "main")
        notes.find_input.setText("nowhere")
        notes.find_next()
        self.assertEqual(notes.current_page, "main")
        self.assertIn("not found", notes.status_label.text())

    def test_quick_add_into_the_checklist_and_shared_list(self):
        window = self.make_window()
        window.quick_add()
        dialog = window._quick_add_dialog
        self.assertEqual(dialog.destination_box.count(), 3, "no shared list outside a household")
        dialog.text_input.setText("  Call the vet ")
        dialog.destination_box.setCurrentIndex(1)
        dialog.accept()
        self.assertEqual([(t["text"], t["recurrence"]) for t in window.storage.get_checklist()],
                         [("Call the vet", "daily")])
        self.assertIsNone(window._quick_add_dialog)

        window.storage.data["household"] = {"id": "h1", "members": [], "tasks": []}
        window.quick_add()
        dialog = window._quick_add_dialog
        self.assertEqual(dialog.destination_box.currentIndex(), 1, "remembers the last list")
        dialog.destination_box.setCurrentIndex(3)
        dialog.text_input.setText("Milk")
        dialog.accept()
        self.assertEqual([t["text"] for t in window.storage.get_household()["tasks"]], ["Milk"])

    def test_quick_add_needs_some_text(self):
        window = self.make_window()
        window.quick_add()
        window._quick_add_dialog._accept_if_text()
        self.assertIsNotNone(window._quick_add_dialog, "still open")
        window._quick_add_dialog.reject()
        self.assertEqual(window.storage.get_checklist(), [])

    def make_synced_window(self):
        """A window whose storage thinks Cloud Sync is on (nothing is sent:
        the sync thread isn't started)."""
        import sync_engine
        self.enable_sync()
        self.storage.save()
        with mock.patch.object(sync_engine.SyncEngine, "start"):
            window = self.make_window()
        return window, window.pomodoro_tab, window.storage

    def outbox(self, st, op):
        return [o["args"] for o in st.data["sync_outbox"] if o["op"] == op]

    def test_shared_timer_start_is_published(self):
        window, tab, st = self.make_synced_window()
        tab.toggle_running()
        [state] = self.outbox(st, "timer_state")
        self.assertEqual((state["phase"], state["running"], state["device"]), ("work", True, st.data["device_id"]))
        self.assertAlmostEqual(state["ends_at"], time.time() + 25 * 60, delta=2)
        tab.toggle_running()
        [state] = self.outbox(st, "timer_state")  # only the newest is kept
        self.assertEqual((state["running"], state["ends_at"]), (False, None))
        self.assertGreater(state["seconds_left"], 24 * 60)

    def test_following_another_devices_timer(self):
        window, tab, st = self.make_synced_window()
        st.data["remote_timer"] = {"phase": "short_break", "running": True, "ends_at": time.time() + 120,
                                   "device": "phone", "rev": 3}
        tab.follow_remote_timer(st.take_remote_timer())
        self.assertEqual((tab.phase, tab.running), ("short_break", True))
        self.assertAlmostEqual(tab.seconds_left, 120, delta=2)
        self.assertEqual(self.outbox(st, "timer_state"), [], "following isn't news to send back")
        self.assertIsNone(st.take_remote_timer(), "each change is followed once")
        st.data["remote_timer"] = {"phase": "short_break", "running": False, "seconds_left": 99,
                                   "device": "phone", "rev": 4}
        tab.follow_remote_timer(st.take_remote_timer())
        self.assertEqual((tab.running, tab.seconds_left), (False, 99))

    def test_our_own_change_coming_back_is_not_followed(self):
        window, tab, st = self.make_synced_window()
        st.data["remote_timer"] = {"phase": "work", "running": True, "ends_at": time.time() + 60,
                                   "device": st.data["device_id"], "rev": 7}
        self.assertIsNone(st.take_remote_timer())
        self.assertEqual(st.data["timer_rev_seen"], 7)

    def test_a_timer_left_running_long_ago_is_ignored(self):
        window, tab, st = self.make_synced_window()
        tab.follow_remote_timer({"phase": "short_break", "running": True, "ends_at": time.time() - 3600,
                                 "device": "phone", "rev": 1})
        self.assertEqual((tab.phase, tab.running), ("work", False))

    def test_the_device_running_the_timer_counts_the_session_once(self):
        window, tab, st = self.make_synced_window()
        tab.toggle_running()
        ends_at = tab._ends_at
        xp = st.data["gamification"]["xp"]
        tab.advance_phase(completed=True)
        self.assertEqual(st.data["gamification"]["xp"], xp + 20)
        [done] = self.outbox(st, "pomodoro")
        self.assertEqual(done["session"], f"work:{round(ends_at)}")
        [state] = self.outbox(st, "timer_state")
        self.assertEqual(state["phase"], "short_break")
        self.assertAlmostEqual(state["ends_at"], ends_at + 5 * 60, delta=0.01, msg="chained onto the last end")

    def test_a_follower_leaves_the_count_to_the_owner_then_takes_over(self):
        window, tab, st = self.make_synced_window()
        xp = st.data["gamification"]["xp"]
        ends_at = time.time() - 1
        tab.follow_remote_timer({"phase": "work", "running": True, "ends_at": ends_at, "device": "phone", "rev": 2})
        tab._on_tick()  # the phase is over
        self.assertEqual(tab.phase, "short_break")
        self.assertEqual(st.data["gamification"]["xp"], xp, "the phone counts it")
        self.assertEqual(self.outbox(st, "pomodoro"), [])
        tab._take_over(tab._pending_takeover)  # ...but the phone never reported back
        self.assertEqual(st.data["gamification"]["xp"], xp + 20)
        self.assertEqual(self.outbox(st, "pomodoro")[0]["session"], f"work:{round(ends_at)}")
        self.assertTrue(tab._owner)

    def test_owner_reporting_back_cancels_the_takeover(self):
        window, tab, st = self.make_synced_window()
        tab.follow_remote_timer({"phase": "work", "running": True, "ends_at": time.time() - 1,
                                 "device": "phone", "rev": 2})
        tab._on_tick()
        pending = tab._pending_takeover
        tab.follow_remote_timer({"phase": "short_break", "running": True, "ends_at": time.time() + 299,
                                 "device": "phone", "rev": 3})
        xp = st.data["gamification"]["xp"]
        tab._take_over(pending)
        self.assertEqual(st.data["gamification"]["xp"], xp)

    def test_an_idle_computer_does_not_pause_the_phones_session(self):
        window, tab, st = self.make_synced_window()
        tab.follow_remote_timer({"phase": "work", "running": True, "ends_at": time.time() + 600,
                                 "device": "phone", "rev": 2})
        with mock.patch("pomodoro_tab.idle.idle_seconds", return_value=3600):
            tab._check_idle()
        self.assertTrue(tab.running)

    def test_without_sync_nothing_is_shared(self):
        window = self.make_window()
        window.pomodoro_tab.toggle_running()
        self.assertEqual(window.storage.data["sync_outbox"], [])

    def test_running_timer_survives_a_restart(self):
        window = self.make_window()
        window.pomodoro_tab._start()
        window.pomodoro_tab.save_on_quit()
        restarted = self.make_window()
        tab = restarted.pomodoro_tab
        self.assertTrue(tab.running)
        self.assertGreater(tab.seconds_left, 24 * 60)

    def test_restart_within_the_same_clock_tick(self):
        # Windows' clock is coarse: ends_at - now can come out a hair over a
        # full session, which must still resume rather than count as expired.
        window = self.make_window()
        now = time.time()
        state = {"phase": "work", "sessions_completed": 0, "seconds_left": 1500, "running": True,
                 "ends_at": now + 1500.001, "day": None}
        window.storage.set_timer_state(state)
        with mock.patch("pomodoro_tab.time.time", return_value=now):
            restarted = self.make_window()
        self.assertTrue(restarted.pomodoro_tab.running)
        self.assertEqual(restarted.pomodoro_tab.seconds_left, 1500)

    def test_timer_that_ran_out_while_closed_is_not_credited(self):
        window = self.make_window()
        window.pomodoro_tab._start()
        state = window.storage.get_timer_state()
        state["ends_at"] = time.time() - 60
        window.storage.set_timer_state(state)
        restarted = self.make_window()
        self.assertFalse(restarted.pomodoro_tab.running)
        self.assertEqual(restarted.storage.get_gamification()["total_pomodoros"], 0)
        self.assertEqual(restarted.pomodoro_tab.seconds_left, 25 * 60)

    def test_paused_timer_keeps_its_time(self):
        window = self.make_window()
        tab = window.pomodoro_tab
        tab.seconds_left = 600
        tab._save_timer_state()
        self.assertEqual(self.make_window().pomodoro_tab.seconds_left, 600)

    def test_reminder_banner_snooze(self):
        window = self.make_window()
        st = window.storage
        task = st.add_task("Water", "daily")
        st.snooze_task_reminder(task["id"], 0)
        window._check_reminders()
        banner = window.checklist_tab.reminder_banner
        self.assertFalse(banner.isHidden())
        window.checklist_tab._banner_snooze(15)
        self.assertTrue(banner.isHidden())
        self.assertIsNotNone(st._find_task(task["id"])["snoozed_until"])

    def test_sync_slows_down_while_hidden(self):
        window = self.make_window()
        window.show()
        self.assertFalse(window.storage.sync.background)
        window.hide()
        self.assertTrue(window.storage.sync.background)
        window._enter_widget_mode()
        self.assertFalse(window.storage.sync.background)


    def test_export_zip(self):
        import os
        import zipfile
        import exporter
        window = self.make_window()
        st = window.storage
        st.set_notes("<p><b>Main</b> notes</p>")
        page = st.add_note_page("Ideas / plans")
        st.set_page_html(page, "<p>an idea</p>")
        st.add_task("Walk", "daily", reminder_time="09:00")
        path = os.path.join(self.tmp, "export.zip")
        self.assertEqual(exporter.export_zip(st, path), 2)
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            self.assertIn("notes/01 Notes.md", names)
            self.assertIn("notes/02 Ideas  plans.md", names)
            # (bold comes out as **Main** with real fonts; Windows' offscreen
            # test platform has none, so only the text is checked here --
            # WINDOWS_TESTING.md covers it on a real desktop)
            self.assertIn("Main", zf.read("notes/01 Notes.md").decode())
            self.assertIn("- [ ] Walk (daily, reminder 09:00)", zf.read("checklist.md").decode())
        # and it restores
        from backup_dialog import BackupDialog  # noqa: F401  (imports cleanly)
        with zipfile.ZipFile(path) as zf:
            data_path = os.path.join(self.tmp, "data.json.restore")
            with open(data_path, "wb") as f:
                f.write(zf.read(exporter.DATA_NAME))
        st.set_notes("changed")
        st.restore_from_file(data_path)
        self.assertEqual(st.get_page_html(page), "<p>an idea</p>")

    def test_progress_shows_focus_by_task(self):
        window = self.make_window()
        task = window.storage.add_task("Report", "once")
        window.storage.record_pomodoro_completed(25, task_id=task["id"])
        window.progress_tab.refresh()
        self.assertEqual(window.progress_tab.task_focus_layout.count(), 1)

    def test_checklist_shows_dates_and_steps(self):
        from datetime import date, timedelta
        window = self.make_window()
        st = window.storage
        tab = window.checklist_tab
        tab.text_input.setText("Rent")
        tab.recurrence_box.setCurrentIndex(tab.recurrence_box.findData("monthly"))
        self.assertTrue(tab.month_day_spin.isVisibleTo(tab))
        tab.month_day_spin.setValue(3)
        tab.add_task()
        late = st.add_task("Taxes", "once", due_date=(date.today() - timedelta(days=1)).isoformat())
        st.add_subtask(late["id"], "Find receipts")
        st.set_task_note(late["id"], "ask about the deadline")
        tab.refresh()
        self.assertEqual(st.get_checklist()[0]["month_day"], 3)
        self.assertIn("[monthly, 3rd]", tab.recurring_list.item(0).text())
        label = tab.once_list.item(0).text()
        self.assertTrue(label.startswith("⚠ ") and "overdue" in label and "☑ 0/1" in label)
        self.assertIn("Find receipts", tab.once_list.item(0).toolTip())

    def test_one_time_tasks_have_their_own_tab(self):
        window = self.make_window()
        tab = window.checklist_tab
        tab.text_input.setText("Walk")
        tab.add_task()
        self.assertIs(tab.list_widget, tab.recurring_list)
        tab.list_tabs.setCurrentWidget(tab.once_list)
        self.assertEqual(tab.recurrence_box.currentData(), "once")  # adding here makes a one-time task
        tab.text_input.setText("Taxes")
        tab.add_task()
        tab.list_tabs.setCurrentWidget(tab.recurring_list)
        self.assertEqual(tab.recurrence_box.currentData(), "daily")
        tab.recurrence_box.setCurrentIndex(tab.recurrence_box.findData("once"))
        tab.text_input.setText("Email")
        tab.add_task()
        self.assertIs(tab.list_widget, tab.once_list)  # jumps to where it landed
        self.assertEqual([tab.once_list.item(r).text().split("  ")[0] for r in range(tab.once_list.count())],
                         ["Taxes", "Email"])
        self.assertEqual(tab.recurring_list.count(), 1)
        self.assertIn("(2)", tab.list_tabs.tabText(1))

    def test_timer_picks_a_one_time_task(self):
        window = self.make_window()
        st = window.storage
        pomodoro = window.pomodoro_tab
        st.add_task("Walk", "daily")
        pomodoro.refresh_focus_tasks()
        self.assertIsNone(st.get_focus_task())
        self.assertFalse(pomodoro.pick_btn.isEnabled())
        taxes = st.add_task("Taxes", "once")
        email = st.add_task("Email", "once")
        pomodoro.refresh_focus_tasks()  # auto-pick fills an empty timer
        first = st.get_focus_task()["id"]
        self.assertIn(first, (taxes["id"], email["id"]))
        self.assertEqual(pomodoro.focus_combo.currentData(), first)
        pomodoro.pick_random_task()  # 🎲 always changes it
        self.assertNotEqual(st.get_focus_task()["id"], first)
        # Choosing "Nothing in particular" yourself sticks, even on Start.
        pomodoro.focus_combo.setCurrentIndex(0)
        pomodoro._start()
        pomodoro._pause()
        self.assertIsNone(st.get_focus_task())
        # Finishing the task moves on to the other one.
        pomodoro.pick_random_task()
        current = st.get_focus_task()["id"]
        st.set_task_done(current, True)
        pomodoro.refresh_focus_tasks()
        self.assertNotEqual(st.get_focus_task()["id"], current)
        pomodoro.auto_pick_check.setChecked(False)
        st.set_focus_task(None)
        pomodoro.refresh_focus_tasks()
        self.assertIsNone(st.get_focus_task())

    def test_task_details_dialog(self):
        from task_details_dialog import TaskDetailsDialog
        window = self.make_window()
        st = window.storage
        task = st.add_task("Taxes", "once")
        dialog = TaskDetailsDialog(st, task["id"], window)
        dialog.subtask_input.setText("Find receipts")
        dialog._add_subtask()
        dialog.subtask_list.item(0).setCheckState(Qt.CheckState.Checked)
        dialog.due_check.setChecked(True)
        dialog.note_edit.setPlainText("by the 15th")
        dialog.name_edit.setText("Do taxes")
        dialog.reject()
        task = st._find_task(task["id"])
        self.assertEqual(task["subtasks"][0]["text"], "Find receipts")
        self.assertTrue(task["subtasks"][0]["done"])
        self.assertIsNotNone(task["due_date"])
        self.assertEqual((task["note"], task["text"]), ("by the 15th", "Do taxes"))

    def test_work_session_pauses_when_youre_away(self):
        window = self.make_window()
        tab = window.pomodoro_tab
        tab._start()
        with mock.patch("pomodoro_tab.idle.idle_seconds", return_value=60):
            tab._check_idle()
        self.assertTrue(tab.running, "a minute away is under the 5-minute default")
        tab.seconds_left = 1000
        tab._start()
        with mock.patch("pomodoro_tab.idle.idle_seconds", return_value=6 * 60), \
                mock.patch("pomodoro_tab.notify") as notify:
            tab._check_idle()
        self.assertFalse(tab.running)
        self.assertFalse(tab.away_banner.isHidden())
        self.assertEqual(tab.seconds_left, 1000 + 6 * 60, "the time away isn't counted")
        self.assertEqual(window.storage.get_timer_state()["seconds_left"], 1360)
        notify.assert_called_once()
        tab._count_away_time()
        self.assertTrue(tab.running)
        self.assertTrue(tab.away_banner.isHidden())
        self.assertLessEqual(tab.seconds_left, 1000, "\"I was here\" counts it after all")

    def test_away_check_leaves_breaks_and_the_never_setting_alone(self):
        window = self.make_window()
        tab = window.pomodoro_tab
        with mock.patch("pomodoro_tab.idle.idle_seconds", return_value=3600):
            tab.phase = "short_break"
            tab._start()
            tab._check_idle()
            self.assertTrue(tab.running)
            tab.phase = "work"
            tab.idle_spin.setValue(0)
            tab.save_settings()
            tab._check_idle()
            self.assertTrue(tab.running)

    def test_tray_shows_the_timer(self):
        window = self.make_window()
        window.pomodoro_tab._start()
        window._refresh_tray_timer()
        self.assertIn("Pause", window.tray_timer_action.text())
        self.assertIn("left", window.tray.toolTip())


if __name__ == "__main__":
    unittest.main()
