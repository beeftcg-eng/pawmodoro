"""
quick_add_dialog.py - "Add a task…" from the tray menu (or `main.py
--quick-add`, which a desktop keyboard shortcut can run): one line of text
and where it goes -- your checklist (one-off, daily or weekly) or the shared
list -- without opening the main window.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLineEdit, QVBoxLayout

# (label, target, recurrence): target "checklist" or "shared".
DESTINATIONS = [
    ("My checklist: once", "checklist", "once"),
    ("My checklist: daily", "checklist", "daily"),
    ("My checklist: weekly", "checklist", "weekly"),
    ("Shared list", "shared", None),
]


class QuickAddDialog(QDialog):
    def __init__(self, in_household, last_choice=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a task")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)

        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("What needs doing?")
        layout.addWidget(self.text_input)

        self.destination_box = QComboBox()
        for label, target, recurrence in DESTINATIONS:
            if target == "shared" and not in_household:
                continue
            self.destination_box.addItem(label, (target, recurrence))
        self.destination_box.setCurrentIndex(min(last_choice, self.destination_box.count() - 1))
        layout.addWidget(self.destination_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add")
        buttons.accepted.connect(self._accept_if_text)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.text_input.setFocus()

    def _accept_if_text(self):
        if self.text().strip():
            self.accept()

    def text(self):
        return self.text_input.text()

    def destination(self):
        """(target, recurrence) of the chosen list."""
        return self.destination_box.currentData()
