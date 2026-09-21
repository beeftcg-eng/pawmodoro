"""
shared_tab.py - The "Shared" tab: a to-do list (groceries, chores, ...) that
you and your partner both see and tick off, on the desktop apps and the
phone, plus a household invite code to link the two accounts.

Everything else (notes, personal checklist, XP) stays private to each
account; only this list and each other's level/streak/weekly totals (shown on
the Progress tab) are shared. See the "Household" section of
supabase/schema.sql.

Items can be dragged to reorder them, and given a schedule: a one-off with an
exact date and time, or one that repeats daily, every week on a chosen weekday
("every Tuesday") or every month on a chosen day, optionally at a time of day
(see shared_schedule.py). A repeating item un-ticks itself when it comes due
again, and pops a desktop reminder at its time.

Edits here go through the same local-first outbox as everything else (see
storage.py / sync_engine.py), so ticking an item never waits on the network.
Creating, joining or leaving a household are one-off actions and do talk to
the cloud directly, so those need a connection.
"""
import html
from datetime import date, datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QInputDialog, QMessageBox, QApplication,
    QAbstractItemView, QComboBox, QSpinBox, QCheckBox, QDateEdit, QTimeEdit,
    QDialog, QDialogButtonBox
)
from PyQt6.QtCore import Qt, QTimer, QDate, QTime, pyqtSignal

import shared_schedule
import theme
from supabase_sync import SyncError


class ScheduleEditor(QWidget):
    """Repeat + day + time controls for one shared item. value() returns a
    shared_schedule dict; the controls that don't apply to the chosen repeat
    are hidden."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.repeat_box = QComboBox()
        for label, key in (("One time", "once"), ("Every day", "daily"),
                           ("Every week", "weekly"), ("Every month", "monthly")):
            self.repeat_box.addItem(label, key)
        self.repeat_box.currentIndexChanged.connect(self._sync_visibility)
        layout.addWidget(self.repeat_box)

        self.weekday_box = QComboBox()
        self.weekday_box.addItems(["on " + name for name in shared_schedule.WEEKDAY_NAMES])
        self.weekday_box.setToolTip("Which day of the week, e.g. \"every Tuesday\"")
        layout.addWidget(self.weekday_box)

        self.month_day_box = QSpinBox()
        self.month_day_box.setRange(1, 31)
        self.month_day_box.setPrefix("on day ")
        self.month_day_box.setToolTip("Day of the month. In months that are too short, the last day is used.")
        layout.addWidget(self.month_day_box)

        self.date_check = QCheckBox("On")
        self.date_check.setToolTip("Give this one-off item an exact date")
        self.date_check.toggled.connect(self._sync_enabled)
        layout.addWidget(self.date_check)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("ddd d MMM yyyy")
        layout.addWidget(self.date_edit)

        self.time_check = QCheckBox("At")
        self.time_check.setToolTip("Give this item a time of day (you get a reminder then)")
        self.time_check.toggled.connect(self._sync_enabled)
        layout.addWidget(self.time_check)
        self.time_edit = QTimeEdit(QTime(9, 0))
        self.time_edit.setDisplayFormat("HH:mm")
        layout.addWidget(self.time_edit)
        layout.addStretch()

        self._sync_visibility()

    def _sync_visibility(self, _index=None):
        repeat = self.repeat_box.currentData()
        self.weekday_box.setVisible(repeat == "weekly")
        self.month_day_box.setVisible(repeat == "monthly")
        self.date_check.setVisible(repeat == "once")
        self.date_edit.setVisible(repeat == "once")
        self._sync_enabled()

    def _sync_enabled(self, _checked=None):
        self.date_edit.setEnabled(self.date_check.isChecked())
        self.time_edit.setEnabled(self.time_check.isChecked())

    def value(self):
        repeat = self.repeat_box.currentData()
        due_time = self.time_edit.time().toString("HH:mm") if self.time_check.isChecked() else None
        due_date = None
        if repeat == "once" and (self.date_check.isChecked() or due_time):
            due_date = self.date_edit.date().toString("yyyy-MM-dd") if self.date_check.isChecked() else date.today().isoformat()
        return shared_schedule.normalize({
            "recurrence": repeat,
            "weekday": self.weekday_box.currentIndex(),
            "month_day": self.month_day_box.value(),
            "due_date": due_date,
            "due_time": due_time,
        })

    def set_value(self, schedule):
        s = shared_schedule.normalize(schedule)
        self.repeat_box.setCurrentIndex(max(0, self.repeat_box.findData(s["recurrence"])))
        self.weekday_box.setCurrentIndex(s["weekday"] if s["weekday"] is not None else date.today().weekday())
        self.month_day_box.setValue(s["month_day"] or date.today().day)
        if s["due_date"]:
            self.date_edit.setDate(QDate.fromString(s["due_date"], "yyyy-MM-dd"))
        else:
            self.date_edit.setDate(QDate.currentDate())
        self.date_check.setChecked(bool(s["due_date"]))
        if s["due_time"]:
            self.time_edit.setTime(QTime.fromString(s["due_time"], "HH:mm"))
        self.time_check.setChecked(bool(s["due_time"]))
        self._sync_visibility()

    def reset(self):
        self.set_value(shared_schedule.DEFAULT_SCHEDULE)
        self.time_edit.setTime(QTime(9, 0))


class ScheduleDialog(QDialog):
    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Schedule")
        layout = QVBoxLayout(self)
        heading = QLabel(f"When is \u201c{task['text']}\u201d for?")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.editor = ScheduleEditor()
        self.editor.set_value({k: task.get(k) for k in shared_schedule.DEFAULT_SCHEDULE})
        layout.addWidget(self.editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def schedule(self):
        return self.editor.value()


class SharedTab(QWidget):
    # emitted after joining/creating/leaving, so other views (Progress) refresh
    household_changed = pyqtSignal()

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self._signature = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        # --- Shown when not in a household ---
        self.setup_box = QWidget()
        setup_layout = QVBoxLayout(self.setup_box)
        self.setup_label = QLabel("")
        self.setup_label.setWordWrap(True)
        setup_layout.addWidget(self.setup_label)
        buttons = QHBoxLayout()
        self.create_btn = QPushButton("Create a household")
        self.create_btn.clicked.connect(self.create_household)
        buttons.addWidget(self.create_btn)
        self.join_btn = QPushButton("Join with an invite code…")
        self.join_btn.clicked.connect(self.join_household)
        buttons.addWidget(self.join_btn)
        buttons.addStretch()
        setup_layout.addLayout(buttons)
        setup_layout.addStretch()
        outer.addWidget(self.setup_box)

        # --- Shown when in a household ---
        self.list_box = QWidget()
        list_layout = QVBoxLayout(self.list_box)
        list_layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.header_label = QLabel("")
        self.header_label.setWordWrap(True)
        header.addWidget(self.header_label, 1)
        copy_btn = QPushButton("Copy invite code")
        copy_btn.clicked.connect(self.copy_code)
        header.addWidget(copy_btn)
        list_layout.addLayout(header)

        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.setToolTip("Drag items to reorder them. Double-click one to set its schedule.")
        self.list_widget.itemChanged.connect(self._on_item_changed)
        self.list_widget.itemDoubleClicked.connect(lambda item: self.edit_schedule())
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        list_layout.addWidget(self.list_widget, 1)

        add_row = QHBoxLayout()
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("e.g. Milk")
        self.text_input.returnPressed.connect(self.add_item)
        add_row.addWidget(self.text_input)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self.add_item)
        add_row.addWidget(add_btn)
        list_layout.addLayout(add_row)

        schedule_row = QHBoxLayout()
        schedule_row.addWidget(QLabel("\U0001F4C5 New item:"))
        self.new_schedule = ScheduleEditor()
        schedule_row.addWidget(self.new_schedule, 1)
        list_layout.addLayout(schedule_row)

        action_row = QHBoxLayout()
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_selected)
        action_row.addWidget(remove_btn)
        schedule_btn = QPushButton("Schedule selected…")
        schedule_btn.setToolTip("Set a date/time, or make the selected item repeat daily, weekly or monthly")
        schedule_btn.clicked.connect(self.edit_schedule)
        action_row.addWidget(schedule_btn)
        clear_btn = QPushButton("Clear completed")
        clear_btn.setToolTip("Removes ticked one-off items. Repeating items stay and un-tick themselves when due again.")
        clear_btn.clicked.connect(self.clear_completed)
        action_row.addWidget(clear_btn)
        action_row.addStretch()
        leave_btn = QPushButton("Leave household…")
        leave_btn.clicked.connect(self.leave_household)
        action_row.addWidget(leave_btn)
        list_layout.addLayout(action_row)

        outer.addWidget(self.list_box)

        self.refresh()

    # ---------- display ----------

    def _current_signature(self):
        """(signature, labels): what's visible right now, so refresh() can skip
        a rebuild when nothing changed. Labels are part of it because they
        carry the schedule text and the "overdue" marker."""
        household = self.storage.get_household()
        labels = {t["id"]: self._label_for(t) for t in (household or {}).get("tasks", [])}
        signature = (
            self.storage.sync_configured(),
            None if not household else (
                household.get("id"), household.get("invite_code"),
                tuple((m.get("name"), m.get("is_me")) for m in household.get("members", [])),
                tuple((t["id"], labels[t["id"]], t["done"]) for t in household.get("tasks", [])),
            ),
        )
        return signature, labels

    def refresh(self, force=False):
        household = self.storage.get_household()
        configured = self.storage.sync_configured()
        signature, labels = self._current_signature()
        # Runs after every cloud poll; skip the rebuild (which would drop the
        # current selection) unless something visible changed.
        if not force and signature == self._signature:
            return
        self._signature = signature

        self.setup_box.setVisible(not household)
        self.list_box.setVisible(bool(household))
        if not household:
            if configured:
                self.setup_label.setText(
                    "Share a to-do list (groceries, chores, errands…) with your partner.\n\n"
                    "One of you creates the household and gives the other the invite code; "
                    "the other joins with it. The list then shows up in both desktop apps and "
                    "on both phones. Your notes, checklist and XP stay private to each of you."
                )
            else:
                self.setup_label.setText(
                    "Shared lists need Cloud Sync. Click ☁️ Sync at the top and log in first, "
                    "then come back here to create or join a household."
                )
            self.create_btn.setEnabled(configured)
            self.join_btn.setEnabled(configured)
            return

        names = [html.escape(m.get("name") or "?") for m in household.get("members", [])]
        if len(names) < 2:
            header = (f"\U0001F3E0 Just you so far — give your partner this invite code: "
                      f"<b>{html.escape(household.get('invite_code', ''))}</b>")
        else:
            header = (f"\U0001F3E0 {' & '.join(names)} — invite code "
                      f"<b>{household.get('invite_code', '')}</b>")
        self.header_label.setText(header)
        self.header_label.setTextFormat(Qt.TextFormat.RichText)

        selected = self.list_widget.currentItem().data(Qt.ItemDataRole.UserRole) if self.list_widget.currentItem() else None
        scroll = self.list_widget.verticalScrollBar().value()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for task in household.get("tasks", []):
            item = QListWidgetItem(labels[task["id"]])
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if task["done"] else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
            if task["id"] == selected:
                self.list_widget.setCurrentItem(item)
        self.list_widget.blockSignals(False)
        self.list_widget.verticalScrollBar().setValue(scroll)

    @staticmethod
    def _label_for(task):
        label = task["text"]
        schedule = {k: task.get(k) for k in shared_schedule.DEFAULT_SCHEDULE}
        described = shared_schedule.describe(schedule)
        if described:
            label += f"   \u2014  {described}"
            if not task["done"] and shared_schedule.is_overdue(shared_schedule.normalize(schedule), datetime.now()):
                label += "  \u26A0 overdue"
        if task["done"] and task.get("done_by_name"):
            label += f"   \u2014 \u2713 {task['done_by_name']}"
        return label

    def refresh_theme(self):
        p = theme.current()
        self.header_label.setStyleSheet(f"color: {p['INK']};")
        self.setup_label.setStyleSheet(f"color: {p['INK_SOFT']};")

    # ---------- list actions (local-first, queued for upload) ----------

    def _on_item_changed(self, item):
        self.storage.shared_set_done(item.data(Qt.ItemDataRole.UserRole), item.checkState() == Qt.CheckState.Checked)
        # show who ticked it -- deferred, since rebuilding the list from
        # inside one of its own items' change signal would delete that item
        # mid-emit
        QTimer.singleShot(0, lambda: self.refresh(force=True))

    def add_item(self):
        text = self.text_input.text().strip()
        if text and self.storage.shared_add_task(text, self.new_schedule.value()):
            self.text_input.clear()
            self.new_schedule.reset()  # a one-off grocery shouldn't inherit the last chore's schedule
            self.refresh(force=True)

    def _on_rows_moved(self):
        self.storage.shared_reorder([
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
        ])
        # The list already shows the new order, so just remember it as the
        # current state instead of rebuilding a list that is mid-drop.
        self._signature = self._current_signature()[0]

    def edit_schedule(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        task = next((t for t in (self.storage.get_household() or {}).get("tasks", [])
                     if t["id"] == item.data(Qt.ItemDataRole.UserRole)), None)
        if task is None:
            return
        dialog = ScheduleDialog(task, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.storage.shared_set_schedule(task["id"], dialog.schedule())
            self.refresh(force=True)

    def remove_selected(self):
        item = self.list_widget.currentItem()
        if item:
            self.storage.shared_remove_task(item.data(Qt.ItemDataRole.UserRole))
            self.refresh(force=True)

    def clear_completed(self):
        self.storage.shared_clear_done()
        self.refresh(force=True)

    def copy_code(self):
        household = self.storage.get_household()
        if household:
            QApplication.clipboard().setText(household.get("invite_code", ""))

    # ---------- household actions (talk to the cloud right now) ----------

    def _default_name(self):
        email = self.storage.get_sync_config().get("email", "")
        return email.split("@")[0].replace(".", " ").title() if email else ""

    def _cloud_call(self, fn):
        """Runs a one-off cloud call with a wait cursor; returns (ok, result)
        and shows the error if it failed."""
        error = None
        result = None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = self.storage.sync.call_now(fn)
        except SyncError as e:
            error = e
        finally:
            QApplication.restoreOverrideCursor()
        if error is None:
            return True, result
        if error.schema_outdated:
            message = ("The cloud database doesn't have the household feature yet. "
                       "Re-run supabase/schema.sql in the Supabase SQL Editor (see MOBILE_SYNC.md).")
        else:
            message = _server_message(str(error))
        QMessageBox.warning(self, "Household", message)
        return False, None

    def create_household(self):
        name, ok = QInputDialog.getText(self, "Create a household", "Your name (shown to your partner):",
                                        text=self._default_name())
        if not ok:
            return
        done, household = self._cloud_call(lambda c: c.household_create(name.strip()))
        if done and household:
            self.storage.set_household(household)
            self.refresh(force=True)
            self.household_changed.emit()

    def join_household(self):
        code, ok = QInputDialog.getText(self, "Join a household", "Invite code:")
        if not ok or not code.strip():
            return
        name, ok = QInputDialog.getText(self, "Join a household", "Your name (shown to your partner):",
                                        text=self._default_name())
        if not ok:
            return
        done, household = self._cloud_call(lambda c: c.household_join(code.strip(), name.strip()))
        if done and household:
            self.storage.set_household(household)
            self.refresh(force=True)
            self.household_changed.emit()

    def leave_household(self):
        answer = QMessageBox.question(
            self, "Leave household",
            "Leave this household? You'll stop seeing the shared list. "
            "If you're the last one in it, the list is deleted.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        done, _ = self._cloud_call(lambda c: c.household_leave())
        if done:
            self.storage.set_household(None)
            self.refresh(force=True)
            self.household_changed.emit()


def _server_message(text):
    """Pulls the human-readable "message" out of a PostgREST error body."""
    import json
    try:
        return json.loads(text.split(": ", 1)[1]).get("message", text)
    except (IndexError, ValueError, AttributeError):
        return text
