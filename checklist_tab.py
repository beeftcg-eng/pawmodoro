"""
checklist_tab.py - Configurable checklist with repeatable
(daily/weekly/once/specific-day) tasks. Daily tasks automatically
un-check themselves at the start of a new day; a "specific day" task
(e.g. "take out the trash" on your collection day) un-checks itself the
next time that weekday comes around - a distinct recurrence type from
plain "weekly", which is unrelated and never auto-resets. Tasks can be
dragged to reorder.

Also renders a separate, independently-reorderable "Card Wishlist"
section (hidden when empty) for tasks pushed here from the Deckbuilder
app's card wishlist — same tab, same underlying checklist_tasks table,
kept apart by a "source" field so it neither mixes into the regular
list nor counts toward the "clear your whole checklist" quest.
"""
from datetime import date

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QComboBox, QLabel, QTimeEdit, QAbstractItemView
)
from PyQt6.QtCore import Qt, QTime, pyqtSignal

import quotes

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class ChecklistTab(QWidget):
    # emitted for anything worth celebrating in-app (title, detail)
    celebrate = pyqtSignal(str, str)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Recurring checklist"))

        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
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
        self.recurrence_box.addItem("daily", "daily")
        self.recurrence_box.addItem("weekly", "weekly")
        self.recurrence_box.addItem("once", "once")
        self.recurrence_box.addItem("specific day", "weekday")
        self.recurrence_box.currentIndexChanged.connect(self._on_recurrence_changed)
        add_row.addWidget(self.recurrence_box)

        self.weekday_box = QComboBox()
        self.weekday_box.addItems(WEEKDAY_NAMES)
        self.weekday_box.setCurrentIndex(date.today().weekday())
        self.weekday_box.setVisible(False)
        self.weekday_box.setToolTip("Which day of the week this task is for (e.g. trash collection day)")
        add_row.addWidget(self.weekday_box)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self.add_task)
        add_row.addWidget(add_btn)

        layout.addLayout(add_row)

        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_selected)
        layout.addWidget(remove_btn)

        # Cards pushed from the Deckbuilder wishlist (see storage's
        # "source" field) — a separate section within this same tab, not a
        # new tab, so it can't be mixed up with (or count toward) the
        # regular checklist above. Hidden entirely when there's nothing in
        # it, so it stays invisible to anyone not using Deckbuilder.
        self.wishlist_label = QLabel("\U0001F0CF Card Wishlist")
        layout.addWidget(self.wishlist_label)

        self.wishlist_list_widget = QListWidget()
        self.wishlist_list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.wishlist_list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        layout.addWidget(self.wishlist_list_widget)

        wishlist_remove_btn = QPushButton("Remove selected")
        wishlist_remove_btn.clicked.connect(self.remove_selected_wishlist)
        layout.addWidget(wishlist_remove_btn)
        self.wishlist_remove_btn = wishlist_remove_btn

        self.list_widget.itemChanged.connect(self._on_item_changed)
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        self.wishlist_list_widget.itemChanged.connect(self._on_item_changed)
        self.wishlist_list_widget.model().rowsMoved.connect(self._on_wishlist_rows_moved)
        self.refresh()

    def _on_recurrence_changed(self, index=None):
        self.weekday_box.setVisible(self.recurrence_box.currentData() == "weekday")

    def refresh(self):
        tasks = self.storage.get_checklist()
        wishlist_tasks = [t for t in tasks if t.get("source") == "wishlist"]

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for task in tasks:
            if task.get("source") == "wishlist":
                continue
            if task["recurrence"] == "weekday" and task.get("weekday") is not None:
                recurrence_label = WEEKDAY_NAMES[task["weekday"]]
            else:
                recurrence_label = task["recurrence"]
            label = f"{task['text']}  \u2014  [{recurrence_label}]"
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

        self.wishlist_list_widget.blockSignals(True)
        self.wishlist_list_widget.clear()
        for task in wishlist_tasks:
            item = QListWidgetItem(task["text"])
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if task.get("completed_today") else Qt.CheckState.Unchecked
            )
            self.wishlist_list_widget.addItem(item)
        self.wishlist_list_widget.blockSignals(False)

        has_wishlist = len(wishlist_tasks) > 0
        self.wishlist_label.setVisible(has_wishlist)
        self.wishlist_list_widget.setVisible(has_wishlist)
        self.wishlist_remove_btn.setVisible(has_wishlist)

    def _on_rows_moved(self):
        ordered_ids = [
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
        ]
        self.storage.reorder_tasks(ordered_ids)

    def _on_wishlist_rows_moved(self):
        ordered_ids = [
            self.wishlist_list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.wishlist_list_widget.count())
        ]
        self.storage.reorder_tasks(ordered_ids)

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
        recurrence = self.recurrence_box.currentData()
        weekday = self.weekday_box.currentIndex() if recurrence == "weekday" else None
        self.storage.add_task(text, recurrence, weekday=weekday)
        self.text_input.clear()
        self.refresh()

    def _remove_selected(self, list_widget):
        item = list_widget.currentItem()
        if item:
            task_id = item.data(Qt.ItemDataRole.UserRole)
            self.storage.remove_task(task_id)
            self.refresh()

    def remove_selected(self):
        self._remove_selected(self.list_widget)

    def remove_selected_wishlist(self):
        self._remove_selected(self.wishlist_list_widget)

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
