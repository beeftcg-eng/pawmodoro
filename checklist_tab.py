"""
checklist_tab.py - Configurable checklist with repeatable
(daily/weekly/monthly/once/specific-day) tasks. Daily tasks automatically
un-check themselves at the start of a new day; a "specific day" task
(e.g. "take out the trash" on your collection day) un-checks itself the
next time that weekday comes around; a monthly one when its day of the
month comes round. A one-off task can have a due date (shown in red once
it's overdue). Any task can have a note and subtasks (Details…). Tasks can
be dragged to reorder. Repeating tasks and one-time ones sit on two
separate tabs ("Recurring" / "One-time"); the Pomodoro tab's 🎲 picks from
the one-time ones.

Also renders a separate, independently-reorderable "Card Wishlist"
section (hidden when empty) for tasks pushed here from the Deckbuilder
app's card wishlist — same tab, same underlying checklist_tasks table,
kept apart by a "source" field so it neither mixes into the regular
list nor counts toward the "clear your whole checklist" quest.
"""
import html
from datetime import date

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QComboBox, QLabel, QTimeEdit, QSpinBox, QAbstractItemView,
    QInputDialog, QCheckBox, QFrame, QDateEdit, QTabWidget
)
from PyQt6.QtCore import Qt, QDate, QTime, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont

import quotes
import task_dates
import theme
from task_details_dialog import TaskDetailsDialog

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
OVERDUE_COLOR = "#d9453b"


class ChecklistTab(QWidget):
    # emitted for anything worth celebrating in-app (title, detail)
    celebrate = pyqtSignal(str, str)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Checklist"))

        # Shown when a reminder fires, so it can be dealt with right here:
        # the desktop notification itself can't carry buttons on every
        # platform. Reminders that fire while one is showing queue up.
        self._due_reminders = []  # task ids, oldest first
        self.reminder_banner = QFrame()
        self.reminder_banner.setObjectName("reminder_banner")
        banner_layout = QVBoxLayout(self.reminder_banner)
        banner_layout.setContentsMargins(8, 6, 8, 6)
        self.reminder_banner_label = QLabel("")
        self.reminder_banner_label.setWordWrap(True)
        banner_layout.addWidget(self.reminder_banner_label)
        banner_buttons = QHBoxLayout()
        for text, handler in (("✅ Done", self._banner_done),
                              ("Snooze 15 min", lambda: self._banner_snooze(15)),
                              ("Snooze 1 h", lambda: self._banner_snooze(60)),
                              ("Dismiss", self._banner_dismiss)):
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            banner_buttons.addWidget(btn)
        banner_buttons.addStretch()
        banner_layout.addLayout(banner_buttons)
        self.reminder_banner.setVisible(False)
        layout.addWidget(self.reminder_banner)

        # Repeating tasks (daily / weekly / monthly / a specific weekday) and
        # one-time ones on separate tabs. `list_widget` is whichever is shown,
        # so the buttons below act on the visible tab's selection.
        self.list_tabs = QTabWidget()
        self.recurring_list = self._make_task_list()
        self.once_list = self._make_task_list()
        self.list_tabs.addTab(self.recurring_list, "")
        self.list_tabs.addTab(self.once_list, "")
        self.list_tabs.currentChanged.connect(self._on_list_tab_changed)
        layout.addWidget(self.list_tabs)

        reminder_row = QHBoxLayout()
        reminder_row.addWidget(QLabel("\U0001F514 Reminder for selected:"))
        self.reminder_mode_box = QComboBox()
        self.reminder_mode_box.addItem("daily at", "time")
        self.reminder_mode_box.addItem("every", "interval")
        self.reminder_mode_box.currentIndexChanged.connect(self._on_reminder_mode_changed)
        reminder_row.addWidget(self.reminder_mode_box)

        self.reminder_time_edit = QTimeEdit()
        self.reminder_time_edit.setDisplayFormat("HH:mm")
        self.reminder_time_edit.setTime(QTime.currentTime())
        reminder_row.addWidget(self.reminder_time_edit)

        self.reminder_hours_spin = QSpinBox()
        self.reminder_hours_spin.setRange(1, 24)
        self.reminder_hours_spin.setValue(8)
        self.reminder_hours_spin.setSuffix(" hours")
        self.reminder_hours_spin.setToolTip(
            "Repeats this often, starting from when you press Set (also on your phone, if it has notifications on)")
        self.reminder_hours_spin.setVisible(False)
        reminder_row.addWidget(self.reminder_hours_spin)

        set_reminder_btn = QPushButton("Set")
        set_reminder_btn.setToolTip("Notify me while the task isn't done yet")
        set_reminder_btn.clicked.connect(self.set_reminder)
        reminder_row.addWidget(set_reminder_btn)

        clear_reminder_btn = QPushButton("Clear")
        clear_reminder_btn.clicked.connect(self.clear_reminder)
        reminder_row.addWidget(clear_reminder_btn)
        reminder_row.addStretch()

        layout.addLayout(reminder_row)

        quiet_row = QHBoxLayout()
        reminders = self.storage.get_reminder_settings()
        self.quiet_check = QCheckBox("\U0001F319 Quiet hours for repeating reminders:")
        self.quiet_check.setToolTip(
            "\"Every N hours\" reminders wait until the quiet hours end, then remind you once")
        self.quiet_check.setChecked(bool(reminders.get("quiet_enabled")))
        quiet_row.addWidget(self.quiet_check)
        self.quiet_start_edit = QTimeEdit(QTime.fromString(reminders.get("quiet_start", "22:00"), "HH:mm"))
        self.quiet_start_edit.setDisplayFormat("HH:mm")
        quiet_row.addWidget(self.quiet_start_edit)
        quiet_row.addWidget(QLabel("to"))
        self.quiet_end_edit = QTimeEdit(QTime.fromString(reminders.get("quiet_end", "08:00"), "HH:mm"))
        self.quiet_end_edit.setDisplayFormat("HH:mm")
        quiet_row.addWidget(self.quiet_end_edit)
        quiet_row.addStretch()
        self.quiet_check.toggled.connect(self._save_quiet_hours)
        self.quiet_start_edit.timeChanged.connect(self._save_quiet_hours)
        self.quiet_end_edit.timeChanged.connect(self._save_quiet_hours)
        layout.addLayout(quiet_row)

        add_row = QHBoxLayout()
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("e.g. Walk the dogs")
        self.text_input.returnPressed.connect(self.add_task)
        add_row.addWidget(self.text_input)

        self.recurrence_box = QComboBox()
        self.recurrence_box.addItem("daily", "daily")
        self.recurrence_box.addItem("weekly", "weekly")
        self.recurrence_box.addItem("monthly", "monthly")
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

        self.month_day_spin = QSpinBox()
        self.month_day_spin.setRange(1, 31)
        self.month_day_spin.setValue(date.today().day)
        self.month_day_spin.setPrefix("on the ")
        self.month_day_spin.setToolTip("Day of the month (in a shorter month, the 31st means its last day)")
        self.month_day_spin.setVisible(False)
        add_row.addWidget(self.month_day_spin)

        self.due_check = QCheckBox("due")
        self.due_check.setToolTip("Give this task a due date: it's shown in red once overdue, "
                                  "and its reminder waits until that day")
        self.due_check.setVisible(False)
        add_row.addWidget(self.due_check)
        self.due_date_edit = QDateEdit(QDate.currentDate())
        self.due_date_edit.setCalendarPopup(True)
        self.due_date_edit.setDisplayFormat("ddd d MMM")
        self.due_date_edit.setVisible(False)
        self.due_date_edit.setEnabled(False)
        self.due_check.toggled.connect(self.due_date_edit.setEnabled)
        add_row.addWidget(self.due_date_edit)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self.add_task)
        add_row.addWidget(add_btn)

        layout.addLayout(add_row)

        edit_row = QHBoxLayout()
        details_btn = QPushButton("Details…")
        details_btn.setToolTip("Steps, a note and a due date for the selected task")
        details_btn.clicked.connect(self.open_details)
        edit_row.addWidget(details_btn)
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
        self._details_dialog = None
        for widget in (self.recurring_list, self.once_list):
            widget.itemChanged.connect(self._on_item_changed)
            widget.itemDoubleClicked.connect(lambda item: self.open_details())
            widget.model().rowsMoved.connect(lambda *_, w=widget: self._on_rows_moved(w))
        self.wishlist_list_widget.itemChanged.connect(self._on_item_changed)
        self.wishlist_list_widget.model().rowsMoved.connect(self._on_wishlist_rows_moved)
        self.refresh()
        self.refresh_theme()

    @staticmethod
    def _make_task_list():
        widget = QListWidget()
        widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        widget.setToolTip("Double-click a task for its details: steps, a note, a due date")
        return widget

    @property
    def list_widget(self):
        """The task list on the tab that's showing."""
        return self.list_tabs.currentWidget()

    def _list_for(self, task):
        return self.once_list if task["recurrence"] == "once" else self.recurring_list

    def _on_list_tab_changed(self, index):
        # Adding from the One-time tab makes a one-time task, and vice versa.
        once = self.list_tabs.widget(index) is self.once_list
        if once != (self.recurrence_box.currentData() == "once"):
            self.recurrence_box.setCurrentIndex(self.recurrence_box.findData("once" if once else "daily"))

    def _on_reminder_mode_changed(self, index=None):
        interval = self.reminder_mode_box.currentData() == "interval"
        self.reminder_time_edit.setVisible(not interval)
        self.reminder_hours_spin.setVisible(interval)

    def _on_recurrence_changed(self, index=None):
        recurrence = self.recurrence_box.currentData()
        self.weekday_box.setVisible(recurrence == "weekday")
        self.month_day_spin.setVisible(recurrence == "monthly")
        self.due_check.setVisible(recurrence == "once")
        self.due_date_edit.setVisible(recurrence == "once")

    def refresh(self, force=False):
        tasks = self.storage.get_checklist()
        # Cloud sync calls this every few seconds; rebuilding the lists
        # throws away the current selection (and can interrupt a drag), so
        # only rebuild when something visible actually changed.
        today = date.today()
        signature = [
            (t["id"], t["text"], t["recurrence"], t.get("weekday"), t.get("reminder_time"), t.get("reminder_every_h"),
             t.get("snoozed_until"), t.get("focus_pomodoros", 0),
             bool(t.get("completed_today")), t.get("source"), t.get("month_day"), t.get("due_date"),
             t.get("note"), str(t.get("subtasks")))
            for t in tasks
        ] + [today]  # "due today" turns into "overdue" at midnight
        if not force and signature == self._last_signature:
            return
        if self._details_dialog is not None:
            self._details_dialog.reload()
        if self._due_reminders:
            QTimer.singleShot(0, self._refresh_banner)
        self._last_signature = signature
        task_lists = (self.recurring_list, self.once_list)
        selected = {
            widget: (widget.currentItem().data(Qt.ItemDataRole.UserRole) if widget.currentItem() else None)
            for widget in task_lists + (self.wishlist_list_widget,)
        }
        scroll = {widget: widget.verticalScrollBar().value() for widget in selected}
        wishlist_tasks = [t for t in tasks if t.get("source") == "wishlist"]

        for widget in task_lists:
            widget.blockSignals(True)
            widget.clear()
        pending = {widget: 0 for widget in task_lists}
        for task in tasks:
            if task.get("source") == "wishlist":
                continue
            if task["recurrence"] == "weekday" and task.get("weekday") is not None:
                recurrence_label = WEEKDAY_NAMES[task["weekday"]]
            else:
                recurrence_label = task_dates.describe(task, today)
            due = task_dates.due_status(task, today)
            label = f"{task['text']}  \u2014  [{recurrence_label}]"
            if due == "overdue":
                label = "⚠ " + label + "  overdue"
            subtasks = task.get("subtasks") or []
            if subtasks:
                label += f"  ☑ {sum(1 for s in subtasks if s.get('done'))}/{len(subtasks)}"
            if (task.get("note") or "").strip():
                label += "  \U0001F4DD"
            if task.get("reminder_every_h"):
                label += f"  \U0001F514 every {task['reminder_every_h']}h"
            elif task.get("reminder_time"):
                label += f"  \U0001F514 {task['reminder_time']}"
            if task.get("snoozed_until"):
                label += f"  \U0001F4A4 {task['snoozed_until'][11:16]}"
            if task.get("focus_pomodoros"):
                label += f"  \U0001F345 {task['focus_pomodoros']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if task.get("completed_today") else Qt.CheckState.Unchecked
            )
            if due:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
                if due == "overdue":
                    item.setForeground(QColor(OVERDUE_COLOR))
            tooltip = self._task_tooltip(task)
            if tooltip:
                item.setToolTip(tooltip)
            widget = self._list_for(task)
            widget.addItem(item)
            if not task.get("completed_today"):
                pending[widget] += 1
        for widget in task_lists:
            widget.blockSignals(False)
        for widget, title in ((self.recurring_list, "\U0001F501 Recurring"), (self.once_list, "\U0001F4CC One-time")):
            self.list_tabs.setTabText(self.list_tabs.indexOf(widget),
                                      f"{title} ({pending[widget]})" if pending[widget] else title)

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

    @staticmethod
    def _task_tooltip(task):
        lines = [("☑ " if s.get("done") else "☐ ") + html.escape(s["text"]) for s in task.get("subtasks") or []]
        note = (task.get("note") or "").strip()
        if note:
            lines.append("<i>" + html.escape(note).replace("\n", "<br>") + "</i>")
        return "<br>".join(lines)

    def _on_rows_moved(self, widget):
        # Only this tab's tasks: reorder_tasks keeps the rest in their order.
        ordered_ids = [
            widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(widget.count())
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
        month_day = self.month_day_spin.value() if recurrence == "monthly" else None
        due_date = (self.due_date_edit.date().toString("yyyy-MM-dd")
                    if recurrence == "once" and self.due_check.isChecked() else None)
        task = self.storage.add_task(text, recurrence, weekday=weekday, month_day=month_day, due_date=due_date)
        self.text_input.clear()
        self.due_check.setChecked(False)
        self.refresh()
        self.select_task(task["id"])  # shows the tab it landed on

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

    def open_details(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        self._details_dialog = TaskDetailsDialog(self.storage, item.data(Qt.ItemDataRole.UserRole), self)
        try:
            self._details_dialog.exec()
        finally:
            self._details_dialog = None
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
        if self.reminder_mode_box.currentData() == "interval":
            self.storage.set_task_reminder(task_id, None, every_hours=self.reminder_hours_spin.value())
        else:
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

    def refresh_theme(self):
        p = theme.current()
        self.reminder_banner.setStyleSheet(
            f"QFrame#reminder_banner {{ background: {p['PAPER_LIGHT']}; border: 2px solid {p['ACCENT']};"
            f" border-radius: 8px; }}"
            f"QFrame#reminder_banner QLabel {{ color: {p['INK']}; }}"
        )

    def _save_quiet_hours(self, *_):
        self.storage.set_reminder_settings(
            quiet_enabled=self.quiet_check.isChecked(),
            quiet_start=self.quiet_start_edit.time().toString("HH:mm"),
            quiet_end=self.quiet_end_edit.time().toString("HH:mm"),
        )

    def select_task(self, task_id):
        """Selects a task, switching to the tab it's on."""
        item = self._find_item(task_id)
        if item is not None:
            widget = item.listWidget()
            self.list_tabs.setCurrentWidget(widget)
            widget.setCurrentItem(item)
            widget.scrollToItem(item)

    def _find_item(self, task_id):
        for widget in (self.recurring_list, self.once_list):
            for row in range(widget.count()):
                if widget.item(row).data(Qt.ItemDataRole.UserRole) == task_id:
                    return widget.item(row)
        return None

    # ---------- Reminder banner ----------
    def show_reminder(self, task_id):
        if task_id in self._due_reminders:
            self._due_reminders.remove(task_id)
        self._due_reminders.append(task_id)
        self._refresh_banner()

    def _banner_task(self):
        """The task the banner is about, dropping ids whose task is gone or
        was ticked off in the meantime (here, in the widget, on the phone)."""
        while self._due_reminders:
            task = next((t for t in self.storage.get_checklist() if t["id"] == self._due_reminders[-1]), None)
            if task is not None and not task.get("completed_today"):
                return task
            self._due_reminders.pop()
        return None

    def _refresh_banner(self):
        task = self._banner_task()
        self.reminder_banner.setVisible(task is not None)
        if task is None:
            return
        more = len(self._due_reminders) - 1
        text = f"\U0001F514 Reminder: <b>{html.escape(task['text'])}</b>"
        if more:
            text += f"  (+{more} more)"
        self.reminder_banner_label.setText(text)

    def _banner_done(self):
        task = self._banner_task()
        if task is not None:
            self._due_reminders.pop()
            # Same path as ticking the box (XP, quests, celebration).
            item = self._find_item(task["id"])
            if item is not None:
                item.setCheckState(Qt.CheckState.Checked)
        self._refresh_banner()

    def _banner_snooze(self, minutes):
        task = self._banner_task()
        if task is not None:
            self._due_reminders.pop()
            self.storage.snooze_task_reminder(task["id"], minutes)
            self.refresh()
        self._refresh_banner()

    def _banner_dismiss(self):
        if self._banner_task() is not None:
            self._due_reminders.pop()
        self._refresh_banner()

    def pending_tasks(self):
        """Used by widget mode: tasks not yet done today."""
        return [t for t in self.storage.get_checklist() if not t.get("completed_today")]
