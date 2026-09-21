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
    QPushButton, QLineEdit, QComboBox, QLabel, QTimeEdit, QAbstractItemView,
    QInputDialog
)
from PyQt6.QtCore import Qt, QTime, QTimer, pyqtSignal

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
        self.list_widget.setToolTip("Double-click a task to rename it")
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

        edit_row = QHBoxLayout()
        edit_btn = QPushButton("Rename selected…")
        edit_btn.clicked.connect(self.rename_selected)
        edit_row.addWidget(edit_btn)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_selected)
        edit_row.addWidget(remove_btn)
        layout.addLayout(edit_row)

        # Shown for a few seconds after removing a task, so a misclick isn't
        # permanent.
        self.undo_btn = QPushButton()
        self.undo_btn.setVisible(False)
        self.undo_btn.clicked.connect(self.undo_remove)
        layout.addWidget(self.undo_btn)
        self._undo_entry = None  # (task, index) of the last removal
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.timeout.connect(self._clear_undo)

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

        self._last_signature = None
        self.list_widget.itemChanged.connect(self._on_item_changed)
        self.list_widget.itemDoubleClicked.connect(lambda item: self.rename_selected())
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        self.wishlist_list_widget.itemChanged.connect(self._on_item_changed)
        self.wishlist_list_widget.model().rowsMoved.connect(self._on_wishlist_rows_moved)
        self.refresh()

    def _on_recurrence_changed(self, index=None):
        self.weekday_box.setVisible(self.recurrence_box.currentData() == "weekday")

    def refresh(self, force=False):
        tasks = self.storage.get_checklist()
        # Cloud sync calls this every few seconds; rebuilding the lists
        # throws away the current selection (and can interrupt a drag), so
        # only rebuild when something visible actually changed.
        signature = [
            (t["id"], t["text"], t["recurrence"], t.get("weekday"), t.get("reminder_time"),
             bool(t.get("completed_today")), t.get("source"))
            for t in tasks
        ]
        if not force and signature == self._last_signature:
            return
        self._last_signature = signature
        selected = {
            widget: (widget.currentItem().data(Qt.ItemDataRole.UserRole) if widget.currentItem() else None)
            for widget in (self.list_widget, self.wishlist_list_widget)
        }
        scroll = {widget: widget.verticalScrollBar().value() for widget in selected}
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

        for widget, task_id in selected.items():
            if task_id is not None:
                for row in range(widget.count()):
                    if widget.item(row).data(Qt.ItemDataRole.UserRole) == task_id:
                        widget.setCurrentRow(row)
                        break
            widget.verticalScrollBar().setValue(scroll[widget])

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
        task = next((t for t in self.storage.get_checklist() if t["id"] == task_id), None)
        result = self.storage.set_task_done(task_id, done)
        if task is not None and done and "xp_gained" in result:
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
            removed = self.storage.remove_task(task_id)
            self.refresh()
            if removed:
                self._offer_undo(*removed)

    def _offer_undo(self, task, index):
        self._undo_entry = (task, index)
        text = task["text"] if len(task["text"]) <= 28 else task["text"][:27] + "…"
        self.undo_btn.setText(f"↩ Undo remove: {text}")
        self.undo_btn.setVisible(True)
        self._undo_timer.start(10000)

    def _clear_undo(self):
        self._undo_entry = None
        self.undo_btn.setVisible(False)

    def undo_remove(self):
        if self._undo_entry:
            self.storage.restore_task(*self._undo_entry)
        self._clear_undo()
        self.refresh()

    def rename_selected(self):
        item = self.list_widget.currentItem() or self.wishlist_list_widget.currentItem()
        if not item:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        task = next((t for t in self.storage.get_checklist() if t["id"] == task_id), None)
        if task is None:
            return
        text, ok = QInputDialog.getText(self, "Rename task", "Task:", text=task["text"])
        text = text.strip()
        if ok and text and text != task["text"]:
            self.storage.rename_task(task_id, text)
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
