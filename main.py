#!/usr/bin/env python3
"""
Pawmodoro - notes + recurring checklist + pomodoro timer + YouTube Music
player, with a pinnable desktop widget mode. Built for Linux (tested target:
Bazzite / KDE Plasma), works on any distro with Python 3 + PyQt6.

Run: python3 main.py
"""
import os
import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QSystemTrayIcon, QMenu, QMessageBox,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
)
from PyQt6.QtGui import QIcon, QAction, QActionGroup
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from storage import Storage
from notes_checklist_tab import NotesChecklistTab
from pomodoro_tab import PomodoroTab
from progress_tab import ProgressTab
from shared_tab import SharedTab
from music_tab import MusicTab
from widget_window import WidgetWindow
from player_bar import PlayerBar
from spotify_client import SpotifyClient
from toast import CelebrationToast
from sync_settings_dialog import SyncSettingsDialog
import notifier
import theme
import gamification
from version import VERSION

ICON_PATH = os.path.join(os.path.dirname(__file__), "resources", "icon.png")

# Single-instance guard: Pawmodoro hides to the system tray rather than
# minimizing, so once that's happened there's no taskbar-visible window
# left for Windows/the desktop to "just focus" when you click the icon
# again - without this, a second click launches a whole second instance
# (two processes fighting over the same data.json, two tray icons, etc).
SINGLE_INSTANCE_KEY = "Pawmodoro-instance-lock"


class MainWindow(QMainWindow):
    # Carries a cloud pull (remote state, household, local revision) from the
    # sync thread to the UI thread; Qt queues the delivery, so the slot always
    # runs on the main thread. See sync_engine.py.
    remote_pulled = pyqtSignal(object, object, int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Pawmodoro v{VERSION}")
        self.resize(900, 650)

        self.storage = Storage()
        theme.set_current(self.storage.get_theme())
        self.spotify_client = SpotifyClient(self.storage)

        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.tabs = QTabWidget()
        self.notes_checklist_tab = NotesChecklistTab(self.storage)
        self.pomodoro_tab = PomodoroTab(self.storage)
        self.progress_tab = ProgressTab(self.storage)
        self.shared_tab = SharedTab(self.storage)
        self.music_tab = MusicTab(self.spotify_client)

        self.tabs.addTab(self.notes_checklist_tab, "Notes && Checklist")
        self.tabs.addTab(self.pomodoro_tab, "Pomodoro")
        self.tabs.addTab(self.progress_tab, "\U0001F3C6 Progress")
        self.tabs.addTab(self.shared_tab, "\U0001F3E0 Shared")
        self.tabs.addTab(self.music_tab, "Music")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Player bar stays visible under the tabs no matter which tab is active
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # A header row with a guaranteed-visible theme button — some Plasma
        # setups (global menu / appmenu integration) can make the top menu
        # bar hard to find, so this doesn't rely on that at all.
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 6, 8, 0)
        self.level_indicator = QLabel("")
        header_layout.addWidget(self.level_indicator)
        header_layout.addStretch()
        self.sync_btn = QPushButton("☁️ Sync")
        self.sync_btn.clicked.connect(self._open_sync_settings)
        header_layout.addWidget(self.sync_btn)
        self.theme_btn = QPushButton("\U0001F3A8 Theme")
        header_layout.addWidget(self.theme_btn)
        central_layout.addWidget(header)

        central_layout.addWidget(self.tabs)

        self.player_bar = PlayerBar()
        central_layout.addWidget(self.player_bar)

        self.setCentralWidget(central)
        self.player_bar.set_spotify_client(self.spotify_client)

        # A floating, non-blocking banner (parented to the central widget so
        # it overlays tabs + player bar) that celebrates finished pomodoros,
        # completed tasks, level-ups, and finished daily quests.
        self.toast = CelebrationToast(central)

        # keep a direct handle to the checklist widget for widget-mode refreshes
        self.checklist_tab = self.notes_checklist_tab.checklist_tab

        self.pomodoro_tab.celebrate.connect(self._on_celebrate)
        self.checklist_tab.celebrate.connect(self._on_celebrate)
        self.shared_tab.household_changed.connect(self.progress_tab.refresh)

        self.widget_window = WidgetWindow(self.storage)
        self.widget_window.restore_requested.connect(self.restore_from_widget)
        self.widget_window.celebrate.connect(self._on_celebrate)
        self.widget_window.task_toggled.connect(self.checklist_tab.refresh)
        self.pomodoro_tab.tick.connect(self.widget_window.update_pomodoro)
        self.widget_window.set_spotify_client(self.spotify_client)

        self._refresh_level_indicator()

        self._theme_action_groups = []
        self._build_menu()
        self._build_tray()

        # Checklist task reminders: polled rather than scheduled exactly,
        # since a task's reminder time can be added/changed/cleared at any
        # moment — a short poll interval is simpler and cheap enough here.
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self._check_reminders)
        self.reminder_timer.start(20000)

        # The app can sit in the tray for days: notice when the calendar day
        # changes so daily tasks, quests and streaks roll over at midnight
        # instead of only at the next launch.
        self.day_timer = QTimer(self)
        self.day_timer.timeout.connect(self._check_new_day)
        self.day_timer.start(30000)

        # Cloud Sync runs entirely on its own background thread (see
        # sync_engine.py): it uploads queued edits and periodically pulls
        # changes made elsewhere (e.g. the phone). This just receives its
        # results on the UI thread and shows its status.
        self._last_sync_status = None
        self.remote_pulled.connect(self._on_remote_pulled)
        self.storage.sync.on_pulled = self.remote_pulled.emit
        self.storage.sync.start()
        self.sync_status_timer = QTimer(self)
        self.sync_status_timer.timeout.connect(self._refresh_sync_status)
        self.sync_status_timer.start(2000)
        self._refresh_sync_status()

        state = self.storage.get_window_state()
        if state.get("widget_mode"):
            self._enter_widget_mode(restore_position=True)

    def _on_tab_changed(self, index):
        # Re-rolls the Progress tab's quote (and picks up any XP/quest
        # changes) each time you switch to it, rather than only on load.
        if self.tabs.widget(index) is self.progress_tab:
            self.progress_tab.refresh(new_quote=True)

    def _on_remote_pulled(self, remote, household, rev):
        """Runs on the main thread, and is the only place a cloud pull is
        applied to local data. adopt_remote_state refuses (returns False) if
        you've edited anything since the pull began or edits are still
        waiting to upload, so a pull can never overwrite fresh work; the next
        poll simply tries again."""
        notes_tab = self.notes_checklist_tab.notes_tab
        if not self.storage.adopt_remote_state(remote, household, rev, keep_notes=notes_tab.is_busy()):
            return
        self.checklist_tab.refresh()
        self.progress_tab.refresh()
        self._refresh_level_indicator()
        self.widget_window.refresh_tasks()
        self.shared_tab.refresh()
        notes_tab.maybe_reload_from_remote()

    def _check_new_day(self):
        if self.storage.roll_day_if_needed():
            self.checklist_tab.refresh()
            self.shared_tab.refresh()
            self.progress_tab.refresh()
            self._refresh_level_indicator()
            self.widget_window.refresh_tasks()

    def _refresh_sync_status(self):
        engine = self.storage.sync
        if not self.storage.sync_configured():
            self.sync_btn.setText("☁️ Sync")
        elif engine.status == "online" and not engine.pending_count():
            self.sync_btn.setText("☁️ Sync ✓")
        elif engine.status in ("auth", "schema"):
            self.sync_btn.setText("☁️ Sync ⚠")
        elif engine.pending_count():
            self.sync_btn.setText(f"☁️ Sync ↑{engine.pending_count()}")
        else:
            self.sync_btn.setText("☁️ Sync …")
        self.sync_btn.setToolTip(engine.status_text())
        # Say so once when sync needs attention, since it fails quietly otherwise.
        if engine.status != self._last_sync_status and engine.status in ("auth", "schema"):
            notifier.send("Pawmodoro cloud sync", engine.status_text())
        self._last_sync_status = engine.status

    def _open_sync_settings(self):
        dialog = SyncSettingsDialog(self.storage, self)
        dialog.exec()
        # The dialog may have just connected and reconciled state.
        self.checklist_tab.refresh()
        self.progress_tab.refresh()
        self.shared_tab.refresh(force=True)
        self.notes_checklist_tab.notes_tab.reload_from_storage()
        self._refresh_level_indicator()
        self.widget_window.refresh_tasks()
        self._refresh_sync_status()

    def _check_reminders(self):
        for task in self.storage.check_due_reminders():
            notifier.send("Task reminder", task["text"])
        for task in self.storage.check_due_shared_reminders():
            notifier.send("Shared list reminder", task["text"])
        self.shared_tab.refresh()  # cheap; picks up "overdue" markers as time passes

    # ---------- Menu ----------
    def _build_menu(self):
        menu = self.menuBar().addMenu("View")

        widget_action = QAction("Pin as desktop widget", self)
        widget_action.triggered.connect(self._enter_widget_mode)
        menu.addAction(widget_action)

        theme_menu = self._build_theme_menu()
        menu.addMenu(theme_menu)
        self.theme_btn.setMenu(theme_menu)

        sync_action = QAction("Cloud Sync…", self)
        sync_action.triggered.connect(self._open_sync_settings)
        menu.addAction(sync_action)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close_app)
        menu.addAction(quit_action)

    def _build_theme_menu(self):
        theme_menu = QMenu("Color theme", self)
        actions = {}
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for name in theme.palette_names():
            action = QAction(name, self, checkable=True)
            action.setChecked(name == theme.current_name())
            action.triggered.connect(lambda checked, n=name: self._apply_theme(n))
            theme_group.addAction(action)
            theme_menu.addAction(action)
            actions[name] = action
        self._theme_action_groups.append(actions)
        return theme_menu

    def _apply_theme(self, name):
        theme.set_current(name)
        self.storage.set_theme(name)
        for actions in self._theme_action_groups:
            for n, action in actions.items():
                action.setChecked(n == name)
        QApplication.instance().setStyleSheet(theme.build_stylesheet())
        self.notes_checklist_tab.refresh_theme()
        self.pomodoro_tab.refresh_theme()
        self.progress_tab.refresh_theme()
        self.shared_tab.refresh_theme()
        self.music_tab.refresh_theme()
        self.widget_window.refresh_theme()
        self.player_bar.refresh_theme()
        self.toast.refresh_theme()

    # ---------- Gamification ----------
    def _on_celebrate(self, title, detail):
        self.toast.show_toast(title, detail)
        self._refresh_level_indicator()
        self.progress_tab.refresh()
        self.widget_window.refresh_tasks()

    def _refresh_level_indicator(self):
        g = self.storage.get_gamification()
        level, _, _ = gamification.level_from_xp(g["xp"])
        text = f"\U0001F3C6 Lv.{level}"
        streak = g.get("current_streak", 0)
        if streak >= 2:
            text += f"  \U0001F525{streak}"
        self.level_indicator.setText(text)

    # ---------- System tray ----------
    def _build_tray(self):
        self.tray = QSystemTrayIcon(self)
        if os.path.exists(ICON_PATH):
            self.tray.setIcon(QIcon(ICON_PATH))
        else:
            self.tray.setIcon(QIcon.fromTheme("view-list-details", QIcon()))
        self.tray.setToolTip("Pawmodoro")

        tray_menu = QMenu()
        show_action = QAction("Show main window", self)
        show_action.triggered.connect(self.restore_from_widget)
        tray_menu.addAction(show_action)

        widget_action = QAction("Pin as desktop widget", self)
        widget_action.triggered.connect(self._enter_widget_mode)
        tray_menu.addAction(widget_action)

        tray_menu.addMenu(self._build_theme_menu())

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close_app)
        tray_menu.addAction(quit_action)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        notifier.register_tray(self.tray)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.restore_from_widget()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The toast is parented to the central widget, which fills the
        # window; re-center it whenever that changes size. Guarded because
        # Qt can fire resizeEvent (e.g. from the initial self.resize() call)
        # before self.toast exists yet.
        if hasattr(self, "toast"):
            self.toast._reposition()

    # ---------- Widget mode ----------
    def _enter_widget_mode(self, restore_position=False):
        state = self.storage.get_window_state()
        state["widget_mode"] = True
        self.storage.set_window_state(state)

        self.widget_window.refresh_tasks()
        self.widget_window.resize(state.get("widget_w", 260), state.get("widget_h", 360))
        if restore_position:
            self.widget_window.move(state.get("widget_x", 100), state.get("widget_y", 100))
        self.widget_window.show()
        self.hide()

    def bring_to_front(self):
        """Called when a second launch attempt pings us over the
        single-instance IPC socket (see main()) - just surfaces whichever
        window is currently the "real" one, without forcing widget mode
        off the way restore_from_widget() deliberately does."""
        state = self.storage.get_window_state()
        target = self.widget_window if state.get("widget_mode") else self
        target.show()
        target.raise_()
        target.activateWindow()

    def restore_from_widget(self):
        state = self.storage.get_window_state()
        state["widget_mode"] = False
        self.storage.set_window_state(state)

        self.checklist_tab.refresh()
        self.widget_window.hide()
        self.show()
        self.raise_()
        self.activateWindow()

    def close_app(self):
        self.pomodoro_tab.stop_all_ambient()
        self.storage.sync.stop()
        self.storage.save()
        QApplication.quit()

    def closeEvent(self, event):
        # Closing the main window just tucks it into the tray, it doesn't quit.
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Pawmodoro", "Still running in the tray. Right-click the tray icon to quit.",
            QSystemTrayIcon.MessageIcon.Information, 3000
        )


def main():
    print(f"Pawmodoro v{VERSION} starting...")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Pawmodoro")

    # Single-instance guard: if another Pawmodoro is already listening on
    # this name, ping it to surface its window and exit instead of
    # starting a second full instance alongside it.
    probe = QLocalSocket()
    probe.connectToServer(SINGLE_INSTANCE_KEY)
    if probe.waitForConnected(200):
        print("Pawmodoro is already running — bringing its window to the front instead.")
        probe.write(b"show")
        probe.waitForBytesWritten(200)
        probe.disconnectFromServer()
        return
    probe.abort()

    # No existing instance responded. A stale registration from a
    # previous crash can still block a fresh listen() on some platforms,
    # so clear it first - harmless no-op if there wasn't one.
    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    ipc_server = QLocalServer()
    ipc_server.listen(SINGLE_INSTANCE_KEY)

    window = MainWindow()  # loads saved theme + builds widgets first
    app.setStyleSheet(theme.build_stylesheet())
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))

    def _on_ipc_connection():
        conn = ipc_server.nextPendingConnection()
        if conn is None:
            return

        def _handle_ping():
            conn.readAll()
            window.bring_to_front()

        conn.readyRead.connect(_handle_ping)
        conn.disconnected.connect(conn.deleteLater)

    ipc_server.newConnection.connect(_on_ipc_connection)
    window._ipc_server = ipc_server  # keep it alive for the app's lifetime

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
