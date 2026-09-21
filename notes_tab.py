"""
notes_tab.py - Freeform, formattable notes (bold/underline/color/headings/
lists via notes_toolbar.py) that autosave as you type, with a manual
save/export option too.

Notes are stored as HTML so formatting persists across restarts. Plain
text notes saved by older versions of this app load in just fine (no "<"
in them, so they're treated as plain text on load).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QLineEdit
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QKeySequence, QShortcut, QTextCursor, QTextDocument

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

        # The notes text as last loaded from / saved to storage; lets a cloud
        # pull skip reloading (which would reset the cursor and undo history)
        # when nothing actually changed.
        self._last_synced_notes = stored

        self.toolbar = NotesToolbar(self.editor)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.editor)

        # Ctrl+F find bar (hidden until asked for)
        self.find_bar = QWidget()
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(0, 0, 0, 0)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find in notes…")
        self.find_input.returnPressed.connect(self.find_next)
        find_layout.addWidget(self.find_input)
        prev_btn = QPushButton("↑")
        prev_btn.setFixedWidth(30)
        prev_btn.clicked.connect(self.find_previous)
        find_layout.addWidget(prev_btn)
        next_btn = QPushButton("↓")
        next_btn.setFixedWidth(30)
        next_btn.clicked.connect(self.find_next)
        find_layout.addWidget(next_btn)
        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(30)
        close_btn.clicked.connect(self.hide_find)
        find_layout.addWidget(close_btn)
        self.find_bar.setVisible(False)
        layout.addWidget(self.find_bar)
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find_shortcut.activated.connect(self.show_find)
        escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.find_bar)
        escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape_shortcut.activated.connect(self.hide_find)

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

    def is_busy(self):
        """True while an edit hasn't been autosaved yet. Cloud pulls leave
        the notes alone then, so they can never clobber text being typed
        right now. (Once saved, the edit is in the upload queue, which also
        holds off any pull until it has reached the cloud -- so merely
        having the editor focused doesn't need to block phone edits.)"""
        return self._autosave_timer.isActive()

    def maybe_reload_from_remote(self):
        """Called after a cloud pull was applied: refreshes the editor from
        storage if the notes changed elsewhere (e.g. on the phone) and
        you're not in the middle of typing."""
        if self.is_busy() or self.storage.get_notes() == self._last_synced_notes:
            return
        self.reload_from_storage()

    def reload_from_storage(self):
        """Re-reads notes from storage (e.g. after a Cloud Sync connect
        just replaced them) without re-triggering the autosave loop."""
        stored = self.storage.get_notes()
        self._last_synced_notes = stored
        # keep the caret and scroll position, so a change arriving from the
        # phone while you're reading doesn't throw you back to the top
        position = self.editor.textCursor().position()
        scroll = self.editor.verticalScrollBar().value()
        self.editor.blockSignals(True)
        if "<" in stored:
            self.editor.setHtml(stored)
        else:
            self.editor.setPlainText(stored)
        self.editor.blockSignals(False)
        cursor = self.editor.textCursor()
        cursor.setPosition(min(position, max(0, self.editor.document().characterCount() - 1)))
        self.editor.setTextCursor(cursor)
        self.editor.verticalScrollBar().setValue(scroll)
        self.status_label.setText("Autosaved")

    def save_now(self):
        self._autosave_timer.stop()  # this save covers whatever the timer was waiting to write
        html = self.editor.toHtml()
        self.storage.set_notes(html)
        self._last_synced_notes = html
        self.status_label.setText("Autosaved")

    # ---------- Find ----------
    def show_find(self):
        self.find_bar.setVisible(True)
        selected = self.editor.textCursor().selectedText()
        if selected and "\u2029" not in selected:
            self.find_input.setText(selected)
        self.find_input.setFocus()
        self.find_input.selectAll()

    def hide_find(self):
        self.find_bar.setVisible(False)
        self.editor.setFocus()

    def _find(self, backward):
        text = self.find_input.text()
        if not text:
            return
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if not self.editor.find(text, flags):
            # wrap around to the other end and try once more
            cursor = self.editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
            self.editor.setTextCursor(cursor)
            if not self.editor.find(text, flags):
                self.status_label.setText(f"“{text}” not found")

    def find_next(self):
        self._find(backward=False)

    def find_previous(self):
        self._find(backward=True)

    def export_txt(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export notes", "notes.txt", "Text files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
            self.status_label.setText(f"Exported to {path}")
