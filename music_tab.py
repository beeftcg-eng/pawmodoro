"""
music_tab.py - No embedded browser anymore (that was QtWebEngine, a full
Chromium instance, which kept background network connections alive even
while minimized — the actual cause of the app's heavy network/resource
usage). This tab is now just: a button to open YouTube Music in your
regular system browser, and the optional Spotify Connect panel.

The bottom player bar controls music playing in your real browser tab via
MPRIS/playerctl (Linux), or Spotify directly via its Web API — see
player_bar.py and mpris.py.
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QFrame
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices

import theme

YOUTUBE_MUSIC_URL = "https://music.youtube.com"


class MusicTab(QWidget):
    spotify_auth_result = pyqtSignal(bool, str)  # emitted from a background thread; queued to the UI thread

    def __init__(self, spotify_client, parent=None):
        super().__init__(parent)
        self.spotify_client = spotify_client

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        outer.setSpacing(12)

        outer.addWidget(self._build_youtube_card())
        outer.addWidget(self._build_spotify_panel())

        self.refresh_theme()

    def _build_youtube_card(self):
        self.youtube_card = QFrame()
        card_layout = QVBoxLayout(self.youtube_card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(14)

        title = QLabel("\U0001F3B5  YouTube Music")
        title.setFont(theme.serif_font(16, bold=True))
        card_layout.addWidget(title)

        self.youtube_blurb = QLabel(
            "Opens in your regular browser \u2014 already logged in with your\n"
            "saved credentials there. The player bar below can control it\n"
            "once it's playing."
        )
        card_layout.addWidget(self.youtube_blurb)

        open_btn = QPushButton("Open YouTube Music")
        open_btn.setFont(theme.serif_font(11))
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(YOUTUBE_MUSIC_URL)))
        card_layout.addWidget(open_btn)

        return self.youtube_card

    def _build_spotify_panel(self):
        self.spotify_card = QFrame()
        card_layout = QHBoxLayout(self.spotify_card)
        card_layout.setContentsMargins(10, 6, 10, 6)

        self.spotify_status_label = QLabel("")
        card_layout.addWidget(self.spotify_status_label)

        self.spotify_client_id_input = QLineEdit()
        self.spotify_client_id_input.setPlaceholderText("Spotify Client ID (see README \u2192 Connecting Spotify)")
        self.spotify_client_id_input.setText(self.spotify_client.client_id())
        self.spotify_client_id_input.setFixedWidth(320)
        card_layout.addWidget(self.spotify_client_id_input)

        self.spotify_connect_btn = QPushButton("Connect Spotify")
        self.spotify_connect_btn.clicked.connect(self._on_spotify_connect_clicked)
        card_layout.addWidget(self.spotify_connect_btn)

        self.spotify_disconnect_btn = QPushButton("Disconnect")
        self.spotify_disconnect_btn.clicked.connect(self._on_spotify_disconnect_clicked)
        card_layout.addWidget(self.spotify_disconnect_btn)

        card_layout.addStretch()

        self.spotify_auth_result.connect(self._on_spotify_auth_result)
        self._refresh_spotify_panel()
        return self.spotify_card

    def _refresh_spotify_panel(self):
        connected = self.spotify_client.is_connected()
        self.spotify_client_id_input.setVisible(not connected)
        self.spotify_connect_btn.setVisible(not connected)
        self.spotify_disconnect_btn.setVisible(connected)
        self.spotify_status_label.setText(
            "\U0001F3A7 Spotify connected" if connected else "\U0001F3A7 Spotify (optional):"
        )

    def _on_spotify_connect_clicked(self):
        self.spotify_client.set_client_id(self.spotify_client_id_input.text())
        self.spotify_connect_btn.setEnabled(False)
        self.spotify_connect_btn.setText("Opening browser to sign in\u2026")
        self.spotify_client.start_authorization(self.spotify_auth_result.emit)

    def _on_spotify_auth_result(self, success, message):
        self.spotify_connect_btn.setEnabled(True)
        self.spotify_connect_btn.setText("Connect Spotify")
        self.spotify_status_label.setText(("\u2705 " if success else "\u26a0\ufe0f ") + message)
        if success:
            self._refresh_spotify_panel()

    def _on_spotify_disconnect_clicked(self):
        self.spotify_client.disconnect()
        self._refresh_spotify_panel()

    def refresh_theme(self):
        p = theme.current()
        self.youtube_card.setStyleSheet(
            f"QFrame {{ background-color: {p['PAPER_LIGHT']}; border: 1px solid {p['PAPER_EDGE']};"
            f" border-radius: 10px; }}"
        )
        self.youtube_blurb.setStyleSheet(f"color: {p['INK_SOFT']};")
        self.spotify_card.setStyleSheet(
            f"QFrame {{ background-color: {p['PAPER_LIGHT']}; border: 1px solid {p['PAPER_EDGE']};"
            f" border-radius: 10px; }}"
        )
        self.spotify_status_label.setStyleSheet(f"color: {p['INK']};")
