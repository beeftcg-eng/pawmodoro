"""
backup_dialog.py - View → Restore from a backup…: pick one of the daily
backups (storage.py keeps the last 14 days) or an exported data file, and
restore it. Pawmodoro restarts afterwards, since every tab caches what it
shows; MainWindow does that on `restored`.
"""
import os
import zipfile
import tempfile

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal

from exporter import DATA_NAME


class BackupDialog(QDialog):
    restored = pyqtSignal()

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle("Restore from a backup")
        self.resize(460, 380)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Pawmodoro keeps a copy of all your data each day (the last 14 days). "
            "Restoring one replaces everything on this computer with it, then restarts the app. "
            "What you have now is kept, so a restore can be undone from this same list."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if storage.sync_configured():
            sync_note = QLabel(
                "☁️ Cloud sync is on: your checklist, notes and XP will come back from the cloud "
                "on the next sync. To roll those back too, turn sync off first (☁️ Sync)."
            )
            sync_note.setWordWrap(True)
            layout.addWidget(sync_note)

        self.list_widget = QListWidget()
        for path, modified in storage.list_backups():
            label = modified.strftime("%A %d %B %Y, %H:%M")
            if os.path.basename(path) == "data-before-restore.json":
                label = f"Before the last restore ({label})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        else:
            self.list_widget.addItem("No backups yet (one is made each day the app runs)")
            self.list_widget.setEnabled(False)
        self.list_widget.itemDoubleClicked.connect(lambda item: self._restore_selected())
        layout.addWidget(self.list_widget)

        buttons = QHBoxLayout()
        file_btn = QPushButton("Choose a file…")
        file_btn.setToolTip("A Pawmodoro export (.zip) or data file (.json)")
        file_btn.clicked.connect(self._choose_file)
        buttons.addWidget(file_btn)
        buttons.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self.restore_btn = QPushButton("Restore")
        self.restore_btn.setEnabled(self.list_widget.isEnabled())
        self.restore_btn.clicked.connect(self._restore_selected)
        buttons.addWidget(self.restore_btn)
        layout.addLayout(buttons)

    def _restore_selected(self):
        item = self.list_widget.currentItem()
        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            self._restore(item.data(Qt.ItemDataRole.UserRole), item.text())

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore from a file", os.path.expanduser("~"),
            "Pawmodoro exports (*.zip *.json);;All files (*)")
        if not path:
            return
        if path.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(path) as zf:
                    data = zf.read(DATA_NAME)
            except (zipfile.BadZipFile, KeyError, OSError):
                QMessageBox.warning(self, "Restore", f"That zip has no {DATA_NAME} in it.")
                return
            handle, extracted = tempfile.mkstemp(suffix=".json")
            with os.fdopen(handle, "wb") as f:
                f.write(data)
            self._restore(extracted, os.path.basename(path), cleanup=extracted)
        else:
            self._restore(path, os.path.basename(path))

    def _restore(self, path, label, cleanup=None):
        try:
            reply = QMessageBox.question(
                self, "Restore", f"Replace everything on this computer with “{label}” and restart Pawmodoro?")
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                self.storage.restore_from_file(path)
            except ValueError as e:
                QMessageBox.warning(self, "Restore", f"Couldn't restore: {e}.")
                return
        finally:
            if cleanup:
                os.remove(cleanup)
        self.accept()
        self.restored.emit()
