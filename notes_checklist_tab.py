"""
notes_checklist_tab.py - Combines the Notes editor and the recurring
Checklist into a single tab, side by side, since they're both "things you
jot on a page" and the user wants them together.
"""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QSplitter
from PyQt6.QtCore import Qt

from notes_tab import NotesTab
from checklist_tab import ChecklistTab


class NotesChecklistTab(QWidget):
    def __init__(self, storage, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.notes_tab = NotesTab(storage)
        self.checklist_tab = ChecklistTab(storage)

        splitter.addWidget(self.notes_tab)
        splitter.addWidget(self.checklist_tab)
        splitter.setStretchFactor(0, 2)  # notes get more room
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([560, 300])

        layout.addWidget(splitter)

    def refresh_theme(self):
        self.notes_tab.refresh_theme()
