"""
notes_tab.py - Freeform, formattable notes (bold/underline/color/headings/
lists via notes_toolbar.py) that autosave as you type, with a manual
save/export option too.

Notes are stored as HTML so formatting persists across restarts. Plain
text notes saved by older versions of this app load in just fine (no "<"
in them, so they're treated as plain text on load).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog
)
from PyQt6.QtCore import QTimer

from ruled_paper_edit import RuledPaperEdit
from notes_toolbar import NotesToolbar
import theme


class NotesTab(QWidget):
    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self.editor = RuledPaperEdit()
        stored = self.storage.get_notes()
        if "<" in stored:
            self.editor.setHtml(stored)
        else:
            self.editor.setPlainText(stored)
        self.editor.setPlaceholderText("Jot anything here. It saves itself.")

        self.toolbar = NotesToolbar(self.editor)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.editor)

        bottom = QHBoxLayout()
        self.status_label = QLabel("Autosaved")
        self.status_label.setObjectName("notes_status_label")
        bottom.addWidget(self.status_label)
        bottom.addStretch()

        save_btn = QPushButton("Save now")
        save_btn.clicked.connect(self.save_now)
        bottom.addWidget(save_btn)

        export_btn = QPushButton("Export to .txt\u2026")
        export_btn.clicked.connect(self.export_txt)
        bottom.addWidget(export_btn)

        layout.addLayout(bottom)

        # Debounced autosave: write 800ms after the user stops typing
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self.save_now)
        self.editor.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self):
        self.status_label.setText("Saving\u2026")
        self._autosave_timer.start(800)

    def refresh_theme(self):
        self.editor.refresh_theme()
        p = theme.current()
        self.status_label.setStyleSheet(f"color: {p['INK_SOFT']}; font-size: 11px;")

    def reload_from_storage(self):
        """Re-reads notes from storage (e.g. after a Cloud Sync connect
        just replaced them) without re-triggering the autosave loop."""
        stored = self.storage.get_notes()
        self.editor.blockSignals(True)
        if "<" in stored:
            self.editor.setHtml(stored)
        else:
            self.editor.setPlainText(stored)
        self.editor.blockSignals(False)
        self.status_label.setText("Autosaved")

    def save_now(self):
        self.storage.set_notes(self.editor.toHtml())
        self.status_label.setText("Autosaved")

    def export_txt(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export notes", "notes.txt", "Text files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
            self.status_label.setText(f"Exported to {path}")
