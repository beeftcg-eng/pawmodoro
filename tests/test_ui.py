"""Headless smoke tests of the real window (skipped without PyQt6)."""
import time
import unittest
from unittest import mock

from support import StorageTestCase

try:
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

    def test_running_timer_survives_a_restart(self):
        window = self.make_window()
        window.pomodoro_tab._start()
        window.pomodoro_tab.save_on_quit()
        restarted = self.make_window()
        tab = restarted.pomodoro_tab
        self.assertTrue(tab.running)
        self.assertGreater(tab.seconds_left, 24 * 60)

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

    def test_tray_shows_the_timer(self):
        window = self.make_window()
        window.pomodoro_tab._start()
        window._refresh_tray_timer()
        self.assertIn("Pause", window.tray_timer_action.text())
        self.assertIn("left", window.tray.toolTip())


if __name__ == "__main__":
    unittest.main()
