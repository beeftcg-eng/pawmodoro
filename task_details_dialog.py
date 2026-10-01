"""
task_details_dialog.py - A checklist task's details: its name, due date (for
a one-off task), a free-text note, and a list of subtasks ("Taxes: find
receipts, fill in the form, submit").

Every change is saved through Storage as it's made (like the rest of the
app), so there's no Save button; the name and note are saved when the dialog
closes or loses focus.
"""
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPlainTextEdit, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QCheckBox, QDateEdit, QInputDialog, QDialogButtonBox
)

import task_dates


class TaskDetailsDialog(QDialog):
    def __init__(self, storage, task_id, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.task_id = task_id
        self.setWindowTitle("Task details")
        self.resize(420, 480)
        task = self._task()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(task["text"])
        self.name_edit.editingFinished.connect(self._save_name)
        form.addRow("Task:", self.name_edit)

        self.schedule_label = QLabel(task_dates.describe(task))
        form.addRow("Repeats:", self.schedule_label)

        # Only one-off tasks have a due date (a repeating one is due every period).
        self.due_row = QHBoxLayout()
        self.due_check = QCheckBox("Due on")
        self.due_date_edit = QDateEdit()
        self.due_date_edit.setCalendarPopup(True)
        self.due_date_edit.setDisplayFormat("ddd d MMM yyyy")
        self.due_row.addWidget(self.due_check)
        self.due_row.addWidget(self.due_date_edit)
        self.due_row.addStretch()
        self.due_check.toggled.connect(self._save_due_date)
        self.due_date_edit.dateChanged.connect(self._save_due_date)
        if task["recurrence"] == "once":
            form.addRow("Due date:", self.due_row)
        else:
            self.due_check.hide()
            self.due_date_edit.hide()
        layout.addLayout(form)

        layout.addWidget(QLabel("Steps:"))
        self.subtask_list = QListWidget()
        self.subtask_list.setToolTip("Tick steps off as you go. Double-click one to rename it.")
        self.subtask_list.itemChanged.connect(self._on_subtask_ticked)
        self.subtask_list.itemDoubleClicked.connect(self._rename_subtask)
        layout.addWidget(self.subtask_list)

        add_row = QHBoxLayout()
        self.subtask_input = QLineEdit()
        self.subtask_input.setPlaceholderText("Add a step, e.g. Find receipts")
        self.subtask_input.returnPressed.connect(self._add_subtask)
        add_row.addWidget(self.subtask_input)
        add_btn = QPushButton("Add")
        add_btn.setAutoDefault(False)
        add_btn.clicked.connect(self._add_subtask)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        step_buttons = QHBoxLayout()
        for text, handler in (("▲", lambda: self._move_subtask(-1)), ("▼", lambda: self._move_subtask(1)),
                              ("Remove step", self._remove_subtask)):
            btn = QPushButton(text)
            btn.setAutoDefault(False)
            btn.clicked.connect(handler)
            step_buttons.addWidget(btn)
        step_buttons.addStretch()
        layout.addLayout(step_buttons)

        layout.addWidget(QLabel("Note:"))
        self.note_edit = QPlainTextEdit(task.get("note") or "")
        self.note_edit.setPlaceholderText("Anything worth remembering about this task")
        self.note_edit.setMaximumHeight(110)
        layout.addWidget(self.note_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.reload()

    def _task(self):
        return next((t for t in self.storage.get_checklist() if t["id"] == self.task_id), None)

    def reload(self):
        """Shows the task as stored now (also after a cloud pull replaced it).
        The note box is left alone, so a pull can't wipe what's being typed."""
        task = self._task()
        if task is None:  # removed, e.g. on the phone
            self.close()
            return
        self.schedule_label.setText(task_dates.describe(task))
        self.due_check.blockSignals(True)
        self.due_date_edit.blockSignals(True)
        due = QDate.fromString(task.get("due_date") or "", "yyyy-MM-dd")
        self.due_check.setChecked(due.isValid())
        self.due_date_edit.setDate(due if due.isValid() else QDate.currentDate())
        self.due_date_edit.setEnabled(due.isValid())
        self.due_check.blockSignals(False)
        self.due_date_edit.blockSignals(False)

        current = self.subtask_list.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.subtask_list.blockSignals(True)
        self.subtask_list.clear()
        for sub in task.get("subtasks") or []:
            item = QListWidgetItem(sub["text"])
            item.setData(Qt.ItemDataRole.UserRole, sub["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if sub.get("done") else Qt.CheckState.Unchecked)
            self.subtask_list.addItem(item)
            if sub["id"] == current_id:
                self.subtask_list.setCurrentItem(item)
        self.subtask_list.blockSignals(False)

    def _selected_subtask_id(self):
        item = self.subtask_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _save_name(self):
        task = self._task()
        text = self.name_edit.text().strip()
        if task is not None and text and text != task["text"]:
            self.storage.rename_task(self.task_id, text)

    def _save_note(self):
        task = self._task()
        note = self.note_edit.toPlainText()
        if task is not None and note != (task.get("note") or ""):
            self.storage.set_task_note(self.task_id, note)

    def _save_due_date(self, *_):
        self.due_date_edit.setEnabled(self.due_check.isChecked())
        due = self.due_date_edit.date().toString("yyyy-MM-dd") if self.due_check.isChecked() else None
        task = self._task()
        if task is not None and due != task.get("due_date"):
            self.storage.set_task_due_date(self.task_id, due)

    def _add_subtask(self):
        text = self.subtask_input.text().strip()
        if not text:
            return
        sub = self.storage.add_subtask(self.task_id, text)
        self.subtask_input.clear()
        self.reload()
        if sub:
            self.subtask_list.setCurrentRow(self.subtask_list.count() - 1)

    def _on_subtask_ticked(self, item):
        self.storage.set_subtask_done(self.task_id, item.data(Qt.ItemDataRole.UserRole),
                                      item.checkState() == Qt.CheckState.Checked)

    def _rename_subtask(self, item):
        text, ok = QInputDialog.getText(self, "Rename step", "Step:", text=item.text())
        text = text.strip()
        if ok and text and text != item.text():
            self.storage.rename_subtask(self.task_id, item.data(Qt.ItemDataRole.UserRole), text)
            self.reload()

    def _move_subtask(self, offset):
        sub_id = self._selected_subtask_id()
        if sub_id:
            self.storage.move_subtask(self.task_id, sub_id, offset)
            self.reload()

    def _remove_subtask(self):
        sub_id = self._selected_subtask_id()
        if sub_id:
            self.storage.remove_subtask(self.task_id, sub_id)
            self.reload()

    def done(self, result):
        # Every way of closing the dialog (Close, Escape, the window's ✕) ends here.
        self._save_name()
        self._save_note()
        super().done(result)
