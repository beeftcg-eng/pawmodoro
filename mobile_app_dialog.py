"""
mobile_app_dialog.py - The dialog behind the header's "Get the mobile app"
button (and View -> Get the mobile app...): the phone app's address, with
buttons to open it or copy it, and the few steps to put it on a home screen.

The phone app is a web app (the `docs/` folder, published on GitHub Pages), so
there is nothing to download from an app store; it only needs the same Cloud
Sync account as this app to show the same notes, checklist and shared list.
"""
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

MOBILE_APP_URL = "https://beeftcg-eng.github.io/pawmodoro/"


class MobileAppDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Get the mobile app")
        self.resize(520, 340)

        layout = QVBoxLayout(self)
        title = QLabel("<b>Pawmodoro on your phone</b>")
        title.setStyleSheet("font-size: 16px;")
        layout.addWidget(title)

        intro = QLabel(
            "The phone app is a web app: nothing to download from an app store. "
            "Open this address in Safari (iPhone) or Chrome (Android):"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        link_row = QHBoxLayout()
        self.link_field = QLineEdit(MOBILE_APP_URL)
        self.link_field.setReadOnly(True)
        self.link_field.setToolTip("Select it and copy it, or send it to your phone")
        link_row.addWidget(self.link_field, 1)
        self.copy_btn = QPushButton("Copy link")
        self.copy_btn.clicked.connect(self.copy_link)
        link_row.addWidget(self.copy_btn)
        open_btn = QPushButton("Open in browser")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(MOBILE_APP_URL)))
        link_row.addWidget(open_btn)
        layout.addLayout(link_row)

        steps = QLabel(
            "<b>Then:</b><ol style='margin-top:2px'>"
            "<li>Tap <i>First time — create account</i> and use the <b>same email and password</b> as "
            "Cloud Sync here (set it up with <i>☁️ Sync</i> in the header), so both show the same data.</li>"
            "<li>Add it to your home screen so it opens like an app: on iPhone tap <i>Share → Add to Home "
            "Screen</i>; on Android tap the browser menu → <i>Install app</i> / <i>Add to Home screen</i>.</li>"
            "</ol>"
        )
        steps.setWordWrap(True)
        layout.addWidget(steps)
        layout.addStretch()

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def copy_link(self):
        QApplication.clipboard().setText(MOBILE_APP_URL)
        self.copy_btn.setText("Copied!")
