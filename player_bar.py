"""
player_bar.py - A persistent playback bar docked at the bottom of the app,
visible on every tab: transport controls, track/position info, and
volume/shuffle/loop.

Two control paths, picked from the same dropdown:
- Spotify (once connected) — controlled directly via its Web API, see
  spotify_client.py.
- Any real MPRIS player (a browser tab playing YouTube Music, etc., on
  Linux) — controlled via `playerctl` (mpris.py).

There used to be a third "in-app" path that drove an embedded YouTube
Music webview via injected JavaScript, but that required QtWebEngine (a
full Chromium instance), which kept background network connections alive
even while minimized. It's gone — the Music tab now just opens YouTube
Music in your regular browser, and this bar controls that via MPRIS.

Transport icons are drawn with QPainter (player_icons.py) rather than
relying on Unicode media-control glyphs, which had inconsistent font
support and were showing up as blank lines on some systems.

Caveat: for MPRIS players, browsers only expose what the Media Session
API supports — play/pause/next/previous work reliably, but volume/shuffle
usually don't. Spotify's own API genuinely supports all of it.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QSlider
)
from PyQt6.QtCore import Qt, QTimer, QUrl, QSize, pyqtSignal
from PyQt6.QtGui import QDesktopServices

import platform
import time
from concurrent.futures import ThreadPoolExecutor
if platform.system() == "Windows":
    import smtc_windows as mpris  # same function names/shapes as mpris.py, see that file
else:
    import mpris
import theme
import player_icons

YOUTUBE_MUSIC_URL = "https://music.youtube.com"
ICON_SIZE = 18
SPOTIFY_LABEL = "Spotify"
STATUS_POLL_MS = 1500
PLAYERS_POLL_SECONDS = 5


_query_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pawmodoro-player")


class PlayerQuery:
    """Runs a player query off the UI thread on Linux, where every query
    spawns a playerctl process that can stall for up to its timeout. On
    Windows SMTC queries are in-process and stay on the calling thread
    (WinRT from a worker thread hasn't been verified there).

    The worker never touches Qt: a timer owned by `parent` collects the
    result on the UI thread and calls on_result(result). If the parent is
    deleted first, its timer goes with it and the result is simply dropped
    (a worker emitting a signal into a deleted widget could crash)."""

    def __init__(self, parent, on_result):
        self._on_result = on_result
        self._future = None
        self._timer = QTimer(parent)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._collect)

    @property
    def busy(self):
        return self._future is not None

    def start(self, fn):
        if platform.system() == "Windows":
            self._on_result(fn())
            return
        self._future = _query_pool.submit(fn)
        self._timer.start()

    def _collect(self):
        future = self._future
        if future is None or not future.done():
            return
        self._future = None
        self._timer.stop()
        try:
            result = future.result()
        except Exception as e:  # noqa: BLE001 - a failed query just means no update
            print(f"[player] query failed: {e}")
            return
        self._on_result(result)


class PlayerBar(QWidget):
    _spotify_status_ready = pyqtSignal(object, object)  # (status_dict_or_None, error_or_None)
    _spotify_command_done = pyqtSignal(object)  # error_or_None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_player = None
        self.spotify_client = None
        self._query = PlayerQuery(self, self._on_poll_done)
        self._players_polled_at = 0.0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(2)

        # --- Row 1: transport + track info ---
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self.player_combo = QComboBox()
        self.player_combo.setFixedWidth(160)
        self.player_combo.currentTextChanged.connect(self._on_player_selected)
        row1.addWidget(self.player_combo)

        self.prev_btn = QPushButton()
        self.prev_btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.prev_btn.setFixedWidth(34)
        self.prev_btn.setToolTip("Previous track")
        self.prev_btn.clicked.connect(lambda: self._command("previous"))
        row1.addWidget(self.prev_btn)

        self.play_btn = QPushButton()
        self.play_btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.play_btn.setFixedWidth(34)
        self.play_btn.setToolTip("Play/Pause")
        self.play_btn.clicked.connect(lambda: self._command("play-pause"))
        row1.addWidget(self.play_btn)

        self.next_btn = QPushButton()
        self.next_btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.next_btn.setFixedWidth(34)
        self.next_btn.setToolTip("Next track")
        self.next_btn.clicked.connect(lambda: self._command("next"))
        row1.addWidget(self.next_btn)

        self.track_label = QLabel("Nothing playing")
        row1.addWidget(self.track_label, stretch=1)

        self.position_label = QLabel("--:-- / --:--")
        self.position_label.setObjectName("caveat_label")
        row1.addWidget(self.position_label)

        open_btn = QPushButton("\U0001F3B5 Open YouTube Music")
        open_btn.clicked.connect(self._open_browser)
        row1.addWidget(open_btn)

        outer.addLayout(row1)

        # --- Row 2: volume, shuffle, loop, status note ---
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        row2.addWidget(QLabel("\U0001F50A"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.sliderReleased.connect(self._on_volume_changed)
        row2.addWidget(self.volume_slider)

        self.shuffle_btn = QPushButton("\U0001F500 Shuffle")
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.clicked.connect(self._toggle_shuffle)
        row2.addWidget(self.shuffle_btn)

        self.loop_btn = QPushButton("\U0001F501 Loop")
        self.loop_btn.setCheckable(True)
        self.loop_btn.clicked.connect(self._toggle_loop)
        row2.addWidget(self.loop_btn)

        self.caveat_label = QLabel("")
        self.caveat_label.setObjectName("caveat_label")
        row2.addWidget(self.caveat_label)
        row2.addStretch()

        outer.addLayout(row2)

        # One timer drives both the player list (every few seconds) and the
        # now-playing status. It does nothing while the bar isn't on screen
        # (the app sitting in the tray, or widget mode), and showEvent
        # catches up immediately when it reappears.
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(STATUS_POLL_MS)

        self._spotify_status_ready.connect(self._on_spotify_status)
        self._spotify_command_done.connect(self._on_spotify_command_done)

        self.refresh_theme()
        self._poll(force_players=True)

    def showEvent(self, event):
        super().showEvent(event)
        self._poll(force_players=True)

    def _poll(self, force_players=False):
        if self._query.busy or not self.isVisible():
            return
        want_players = force_players or time.monotonic() - self._players_polled_at >= PLAYERS_POLL_SECONDS
        player = None if self._is_spotify() else self.current_player
        if not mpris.available():
            if want_players:
                self._players_polled_at = time.monotonic()
                self._apply_players([])
            self._refresh_status()
            return
        if want_players:
            self._players_polled_at = time.monotonic()

        def query():
            players = mpris.list_players() if want_players else None
            # A freshly listed player list may drop the current one; then the
            # status is fetched after the UI has picked a new player.
            if player and (players is None or player in players):
                return players, player, mpris.snapshot(player)
            return players, player, None

        self._query.start(query)

    def _on_poll_done(self, result):
        players, player, snap = result
        if players is not None:
            self._apply_players(players)
        if snap is not None and player == self.current_player:
            self._apply_snapshot(snap)
        elif self._is_spotify() or (players is not None and self.current_player != player):
            self._refresh_status()

    def set_spotify_client(self, spotify_client):
        """Called once from main.py; Spotify only appears in the player
        dropdown once it's actually connected."""
        self.spotify_client = spotify_client
        self._refresh_players()

    def refresh_theme(self):
        """Re-apply colors for the current palette. Call after a theme switch."""
        p = theme.current()
        self.setStyleSheet(
            f"PlayerBar {{ background-color: {p['PAPER_LIGHT']}; border-top: 1px solid {p['PAPER_EDGE']}; }}"
            f"QLabel#caveat_label {{ color: {p['INK_SOFT']}; font-size: 10px; }}"
            f"QLabel {{ color: {p['INK']}; }}"
        )
        self.prev_btn.setIcon(player_icons.prev_icon(p["INK"], ICON_SIZE))
        self.next_btn.setIcon(player_icons.next_icon(p["INK"], ICON_SIZE))
        self._update_play_icon()

    def _update_play_icon(self):
        p = theme.current()
        playing = self.play_btn.property("playing") is True
        icon = player_icons.pause_icon(p["INK"], ICON_SIZE) if playing else player_icons.play_icon(p["INK"], ICON_SIZE)
        self.play_btn.setIcon(icon)

    def _is_spotify(self):
        return self.current_player == SPOTIFY_LABEL

    def _open_browser(self):
        QDesktopServices.openUrl(QUrl(YOUTUBE_MUSIC_URL))

    def _command(self, action):
        if not self.current_player:
            return
        if self._is_spotify():
            fn = {"previous": self.spotify_client.previous_track,
                  "play-pause": self.spotify_client.play_pause,
                  "next": self.spotify_client.next_track}[action]
            self.spotify_client.run_async(fn, self._spotify_command_done.emit)
        else:
            mpris.command(self.current_player, action)
            QTimer.singleShot(300, self._poll)

    def _on_spotify_command_done(self, error):
        if error:
            self.track_label.setText(f"Spotify: {error}")
        QTimer.singleShot(300, self._poll)

    def _on_volume_changed(self):
        if not self.current_player:
            return
        if self._is_spotify():
            self.spotify_client.run_async(
                lambda: self.spotify_client.set_volume(self.volume_slider.value()),
                self._spotify_command_done.emit,
            )
        else:
            mpris.set_volume(self.current_player, self.volume_slider.value() / 100)

    def _toggle_shuffle(self):
        if not self.current_player:
            return
        if self._is_spotify():
            on = self.shuffle_btn.isChecked()
            self.spotify_client.run_async(
                lambda: self.spotify_client.set_shuffle(on), self._spotify_command_done.emit
            )
        else:
            mpris.set_shuffle(self.current_player, "On" if self.shuffle_btn.isChecked() else "Off")

    def _toggle_loop(self):
        if not self.current_player:
            return
        if self._is_spotify():
            mode = "track" if self.loop_btn.isChecked() else "off"
            self.spotify_client.run_async(
                lambda: self.spotify_client.set_repeat(mode), self._spotify_command_done.emit
            )
        else:
            mpris.set_loop_status(self.current_player, "Track" if self.loop_btn.isChecked() else "None")

    def _refresh_players(self):
        self._poll(force_players=True)

    def _apply_players(self, mpris_players):
        players = []
        if self.spotify_client is not None and self.spotify_client.is_connected():
            players.append(SPOTIFY_LABEL)
        players.extend(mpris_players)

        current_text = self.player_combo.currentText()

        self.player_combo.blockSignals(True)
        self.player_combo.clear()
        if not players:
            self.player_combo.addItem("No player detected")
            self.current_player = None
            self.track_label.setText("Open YouTube Music and press play")
        else:
            self.player_combo.addItems(players)
            if current_text in players:
                self.player_combo.setCurrentText(current_text)
                self.current_player = current_text
            else:
                # The previous selection dropped out of the list (e.g. a
                # browser tab reloaded and its player id changed) - if it
                # was a non-Spotify player, stay on a non-Spotify player
                # when one's still available instead of falling back to
                # Spotify, which would otherwise silently and permanently
                # steal the selection back on every later refresh (Spotify
                # trivially stays "in players" once picked).
                was_non_spotify = self.current_player is not None and self.current_player != SPOTIFY_LABEL
                non_spotify = [p for p in players if p != SPOTIFY_LABEL]
                if was_non_spotify and non_spotify:
                    preferred = mpris.pick_default(non_spotify)
                elif SPOTIFY_LABEL in players:
                    preferred = SPOTIFY_LABEL
                else:
                    preferred = mpris.pick_default(players)
                self.player_combo.setCurrentText(preferred)
                self.current_player = preferred
        self.player_combo.blockSignals(False)
        self._update_controls_for_selection()

    def _on_player_selected(self, name):
        if name and name != "No player detected":
            self.current_player = name
            self._update_controls_for_selection()
            self._refresh_status()

    def _update_controls_for_selection(self):
        spotify = self._is_spotify()
        is_windows = platform.system() == "Windows"
        self.volume_slider.setEnabled(spotify or not is_windows)
        self.shuffle_btn.setEnabled(True)
        self.loop_btn.setEnabled(True)
        if spotify:
            self.caveat_label.setText("(controls whatever Spotify Connect device is currently active)")
        elif not mpris.available():
            self.caveat_label.setText(
                "Windows media controls unavailable \u2014 reinstall Pawmodoro to fix this"
                if is_windows else
                "playerctl not found \u2014 install it to control a browser player"
            )
        elif is_windows:
            self.caveat_label.setText("(no volume control on Windows \u2014 shuffle/loop only work with players that support them)")
        else:
            self.caveat_label.setText("(volume/shuffle only work with players that support them \u2014 browsers often don't)")

    def _refresh_status(self):
        if not self.current_player:
            return
        if self._is_spotify():
            if self.spotify_client is not None:
                self.spotify_client.get_full_status_async(self._spotify_status_ready.emit)
            return

        self._poll()

    def _apply_snapshot(self, snap):
        status = snap["status"]
        self.track_label.setText(snap["text"] or status or "Nothing playing")

        self.play_btn.setProperty("playing", status == "Playing")
        self._update_play_icon()

        self.position_label.setText(
            f"{mpris.format_seconds(snap['position'])} / {mpris.format_seconds(snap['length'])}")

        vol = snap["volume"]
        if vol is not None and not self.volume_slider.isSliderDown():
            self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(int(vol * 100))
            self.volume_slider.blockSignals(False)

        if snap["shuffle"] in ("On", "Off"):
            self.shuffle_btn.setChecked(snap["shuffle"] == "On")

        if snap["loop"] in ("None", "Track", "Playlist"):
            self.loop_btn.setChecked(snap["loop"] != "None")

    def _on_spotify_status(self, status, error):
        if not self._is_spotify():
            return  # selection changed while the async call was in flight
        if error:
            self.track_label.setText(f"Spotify: {error}")
            return
        if not status:
            self.track_label.setText("Nothing playing")
            return

        title = status.get("title", "")
        artist = status.get("artist", "")
        text = f"{title} \u2014 {artist}" if artist else title
        self.track_label.setText(text or "Nothing playing")
        self.play_btn.setProperty("playing", bool(status.get("playing")))
        self._update_play_icon()
        self.position_label.setText("--:-- / --:--")

        volume = status.get("volume")
        if volume is not None and not self.volume_slider.isSliderDown():
            self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(int(volume))
            self.volume_slider.blockSignals(False)

        if "shuffle" in status:
            self.shuffle_btn.setChecked(bool(status["shuffle"]))
        if "repeat" in status:
            self.loop_btn.setChecked(status["repeat"] in ("track", "context"))
