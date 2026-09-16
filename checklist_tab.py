"""
checklist_tab.py - Configurable checklist with repeatable (daily/weekly/once) tasks.
Daily tasks automatically un-check themselves at the start of a new day.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QComboBox, QLabel, QTimeEdit
)
from PyQt6.QtCore import Qt, QTime, pyqtSignal

import quotes


class ChecklistTab(QWidget):
    # emitted for anything worth celebrating in-app (title, detail)
    celebrate = pyqtSignal(str, str)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Recurring checklist"))

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        reminder_row = QHBoxLayout()
        reminder_row.addWidget(QLabel("\U0001F514 Reminder for selected:"))
        self.reminder_time_edit = QTimeEdit()
        self.reminder_time_edit.setDisplayFormat("HH:mm")
        self.reminder_time_edit.setTime(QTime.currentTime())
        reminder_row.addWidget(self.reminder_time_edit)

        set_reminder_btn = QPushButton("Set")
        set_reminder_btn.setToolTip("Notify me at this time on days the task isn't done yet")
        set_reminder_btn.clicked.connect(self.set_reminder)
        reminder_row.addWidget(set_reminder_btn)

        clear_reminder_btn = QPushButton("Clear")
        clear_reminder_btn.clicked.connect(self.clear_reminder)
        reminder_row.addWidget(clear_reminder_btn)
        reminder_row.addStretch()

        layout.addLayout(reminder_row)

        add_row = QHBoxLayout()
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("e.g. Walk the dogs")
        self.text_input.returnPressed.connect(self.add_task)
        add_row.addWidget(self.text_input)

        self.recurrence_box = QComboBox()
        self.recurrence_box.addItems(["daily", "weekly", "once"])
        add_row.addWidget(self.recurrence_box)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self.add_task)
        add_row.addWidget(add_btn)

        layout.addLayout(add_row)

        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_selected)
        layout.addWidget(remove_btn)

        self.list_widget.itemChanged.connect(self._on_item_changed)
        self.refresh()

    def refresh(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for task in self.storage.get_checklist():
            label = f"{task['text']}  \u2014  [{task['recurrence']}]"
            if task.get("reminder_time"):
                label += f"  \U0001F514 {task['reminder_time']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if task.get("completed_today") else Qt.CheckState.Unchecked
            )
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

    def _on_item_changed(self, item):
        task_id = item.data(Qt.ItemDataRole.UserRole)
        done = item.checkState() == Qt.CheckState.Checked
        self.storage.set_task_done(task_id, done)

        task = next((t for t in self.storage.get_checklist() if t["id"] == task_id), None)
        if task is not None:
            result = self.storage.record_task_event(task["recurrence"], done)
            if done and "xp_gained" in result:
                detail = f"{task['text']}  •  +{result['xp_gained']} XP"
                if result["new_level"] > result["old_level"]:
                    detail += f"  • Level up! Now level {result['new_level']}"
                congrats = quotes.random_task_congrats()
                self.celebrate.emit(f"✅ {congrats}", detail)
                for quest in result.get("completed_quests", []):
                    self.celebrate.emit(
                        "\U0001F31F Quest complete!",
                        f"{quest['desc']}  • +{quest['bonus_xp']} XP",
                    )

    def add_task(self):
        text = self.text_input.text().strip()
        if not text:
            return
        self.storage.add_task(text, self.recurrence_box.currentText())
        self.text_input.clear()
        self.refresh()

    def remove_selected(self):
        item = self.list_widget.currentItem()
        if item:
            task_id = item.data(Qt.ItemDataRole.UserRole)
            self.storage.remove_task(task_id)
            self.refresh()

    def set_reminder(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        time_str = self.reminder_time_edit.time().toString("HH:mm")
        self.storage.set_task_reminder(task_id, time_str)
        self.refresh()

    def clear_reminder(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        self.storage.set_task_reminder(task_id, None)
        self.refresh()

    def pending_tasks(self):
        """Used by widget mode: tasks not yet done today."""
        return [t for t in self.storage.get_checklist() if not t.get("completed_today")]
