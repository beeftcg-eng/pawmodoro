"""
widget_window.py - A small, frameless, always-on-top window you can leave
sitting on your desktop: shows today's pending checklist items and the
pomodoro countdown. Drag it anywhere; position is remembered.

This is a "pinned mini-window", the practical equivalent of a desktop
widget without needing a full KDE Plasmoid/QML package. It behaves the
way KDE "Keep above others" + "Skip taskbar" windows do.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QFrame, QSizeGrip
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize

import theme
import mpris
import player_icons
import gamification
from circular_timer import CircularTimer

ICON_SIZE = 16
SPOTIFY_LABEL = "Spotify"
# Debounce window geometry saves during an interactive resize (the size
# grip fires resizeEvent continuously while dragging) so we don't hit disk
# with a full JSON write dozens of times per second.
GEOMETRY_SAVE_DELAY_MS = 300


class WidgetWindow(QWidget):
    restore_requested = pyqtSignal()
    _spotify_status_ready = pyqtSignal(object, object)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self._drag_pos = None

        # Created up front: setMinimumSize()/layout below can trigger a
        # resizeEvent before the rest of __init__ runs, and that handler
        # needs this timer to already exist.
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.timeout.connect(self._save_geometry)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(220, 260)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        self.frame = QFrame()
        frame_layout = QVBoxLayout(self.frame)

        title_row = QHBoxLayout()
        self.title_label = QLabel("\U0001F43E Pawmodoro")
        title_row.addWidget(self.title_label)
        title_row.addStretch()
        restore_btn = QPushButton("\u2921")
        restore_btn.setFixedSize(20, 20)
        restore_btn.setToolTip("Restore full window")
        restore_btn.clicked.connect(self.restore_requested.emit)
        title_row.addWidget(restore_btn)
        frame_layout.addLayout(title_row)

        self.circular_timer = CircularTimer(min_side=100)
        self.circular_timer.setFixedSize(140, 140)
        self._last_phase = "work"
        frame_layout.addWidget(self.circular_timer, alignment=Qt.AlignmentFlag.AlignHCenter)

        frame_layout.addWidget(QLabel("Today:"))
        self.tasks_label = QLabel("")
        self.tasks_label.setWordWrap(True)
        frame_layout.addWidget(self.tasks_label)

        # --- Compact music controls, so they're always at hand ---
        player_row = QHBoxLayout()
        self.mini_prev = QPushButton()
        self.mini_play = QPushButton()
        self.mini_next = QPushButton()
        for btn in (self.mini_prev, self.mini_play, self.mini_next):
            btn.setFixedSize(28, 24)
            btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            player_row.addWidget(btn)
        self.mini_track_label = QLabel("")
        self.mini_track_label.setObjectName("mini_track_label")
        self.mini_track_label.setWordWrap(True)
        frame_layout.addLayout(player_row)
        frame_layout.addWidget(self.mini_track_label)

        # a frameless window has no OS-provided resize handles, so add one
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        self.size_grip = QSizeGrip(self.frame)
        grip_row.addWidget(self.size_grip)
        frame_layout.addLayout(grip_row)

        self.current_player = None
        self.spotify_client = None
        self.mini_prev.clicked.connect(lambda: self._mini_command("previous"))
        self.mini_play.clicked.connect(lambda: self._mini_command("play-pause"))
        self.mini_next.clicked.connect(lambda: self._mini_command("next"))
        self._mini_timer = QTimer(self)
        self._mini_timer.timeout.connect(self._refresh_mini_player)
        self._mini_timer.start(2000)
        self._spotify_status_ready.connect(self._on_spotify_mini_status)
        self._update_mini_availability()

        outer.addWidget(self.frame)
        self.refresh_theme()
        self.refresh_tasks()

    def set_spotify_client(self, spotify_client):
        self.spotify_client = spotify_client
        self._update_mini_availability()
        self._refresh_mini_player()

    def _has_any_player_source(self):
        return (
            (self.spotify_client is not None and self.spotify_client.is_connected())
            or mpris.available()
        )

    def _update_mini_availability(self):
        available = self._has_any_player_source()
        for btn in (self.mini_prev, self.mini_play, self.mini_next):
            btn.setEnabled(available)
        if not available:
            self.mini_track_label.setText("playerctl not installed")

    def refresh_theme(self):
        """Re-apply colors for the current palette. Call after a theme switch."""
        p = theme.current()
        self.frame.setStyleSheet(
            f"QFrame {{ background-color: {p['PAPER_LIGHT']}; border: 1px solid {p['PAPER_EDGE']};"
            f" border-radius: 10px; }}"
            f"QLabel {{ color: {p['INK']}; }}"
            f"QLabel#mini_track_label {{ color: {p['INK_SOFT']}; font-size: 10px; }}"
        )
        self.title_label.setFont(theme.serif_font(11, bold=True))
        ring_color = p["ACCENT"] if self._last_phase == "work" else p["ACCENT_SOFT"]
        self.circular_timer.set_colors(ring_color, p["PAPER_EDGE"], p["INK"], p["INK_SOFT"])
        self.mini_prev.setIcon(player_icons.prev_icon(p["INK"], ICON_SIZE))
        self.mini_next.setIcon(player_icons.next_icon(p["INK"], ICON_SIZE))
        self._update_mini_play_icon()

    def _update_mini_play_icon(self):
        p = theme.current()
        playing = self.mini_play.property("playing") is True
        icon = player_icons.pause_icon(p["INK"], ICON_SIZE) if playing else player_icons.play_icon(p["INK"], ICON_SIZE)
        self.mini_play.setIcon(icon)

    def refresh_tasks(self):
        storage = self.storage
        pending = [t for t in storage.get_checklist() if not t.get("completed_today")]
        if not pending:
            self.tasks_label.setText("\u2705 All done for today")
        else:
            self.tasks_label.setText("\n".join(f"\u2022 {t['text']}" for t in pending))

        level, _, _ = gamification.level_from_xp(storage.get_gamification()["xp"])
        self.title_label.setText(f"\U0001F43E Pawmodoro \u2014 Lv.{level}")

    def update_pomodoro(self, time_text, phase_text, fraction, phase_key):
        p = theme.current()
        if phase_key != self._last_phase:
            self._last_phase = phase_key
            ring_color = p["ACCENT"] if phase_key == "work" else p["ACCENT_SOFT"]
            self.circular_timer.set_colors(ring_color, p["PAPER_EDGE"], p["INK"], p["INK_SOFT"])
        self.circular_timer.set_progress(fraction, time_text, phase_text)

    def _mini_command(self, action):
        if not self.current_player:
            return
        if self.current_player == SPOTIFY_LABEL:
            fn = {"previous": self.spotify_client.previous_track,
                  "play-pause": self.spotify_client.play_pause,
                  "next": self.spotify_client.next_track}[action]
            self.spotify_client.run_async(fn, lambda err: QTimer.singleShot(300, self._refresh_mini_player))
        else:
            mpris.command(self.current_player, action)
            QTimer.singleShot(300, self._refresh_mini_player)

    def _refresh_mini_player(self):
        players = []
        if self.spotify_client is not None and self.spotify_client.is_connected():
            players.append(SPOTIFY_LABEL)
        if mpris.available():
            players.extend(mpris.list_players())

        if not players:
            self.current_player = None
            self.mini_track_label.setText("Nothing playing")
            return
        if self.current_player not in players:
            self.current_player = SPOTIFY_LABEL if SPOTIFY_LABEL in players else mpris.pick_default(players)

        if self.current_player == SPOTIFY_LABEL:
            if self.spotify_client is not None:
                self.spotify_client.get_full_status_async(self._spotify_status_ready.emit)
            return

        status = mpris.status(self.current_player)
        text, _length = mpris.now_playing_bundle(self.current_player)
        self.mini_track_label.setText(text or status or "Nothing playing")
        self.mini_play.setProperty("playing", status == "Playing")
        self._update_mini_play_icon()

    def _on_spotify_mini_status(self, status, error):
        if self.current_player != SPOTIFY_LABEL:
            return
        if error:
            self.mini_track_label.setText(f"Spotify: {error}")
            return
        if not status:
            self.mini_track_label.setText("Nothing playing")
            return
        title = status.get("title", "")
        artist = status.get("artist", "")
        text = f"{title} \u2014 {artist}" if artist else title
        self.mini_track_label.setText(text or "Nothing playing")
        self.mini_play.setProperty("playing", bool(status.get("playing")))
        self._update_mini_play_icon()

    # --- make the frameless window draggable ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._save_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Debounced: an interactive drag on the size grip fires this
        # continuously, and each save is a full synchronous JSON write.
        self._geometry_save_timer.start(GEOMETRY_SAVE_DELAY_MS)

    def _save_geometry(self):
        pos = self.pos()
        state = self.storage.get_window_state()
        state["widget_x"] = pos.x()
        state["widget_y"] = pos.y()
        state["widget_w"] = self.width()
        state["widget_h"] = self.height()
        self.storage.set_window_state(state)
