"""
shared_tab.py - The "Shared" tab: a to-do list (groceries, chores, ...) that
you and your partner both see and tick off, on the desktop apps and the
phone, plus a household invite code to link the two accounts.

Everything else (notes, personal checklist, XP) stays private to each
account; only this list and each other's level/streak/weekly totals (shown on
the Progress tab) are shared. See the "Household" section of
supabase/schema.sql.

Edits here go through the same local-first outbox as everything else (see
storage.py / sync_engine.py), so ticking an item never waits on the network.
Creating, joining or leaving a household are one-off actions and do talk to
the cloud directly, so those need a connection.
"""
import html

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QInputDialog, QMessageBox, QApplication
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

import theme
from supabase_sync import SyncError


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
        self.list_widget.itemChanged.connect(self._on_item_changed)
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

        action_row = QHBoxLayout()
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_selected)
        action_row.addWidget(remove_btn)
        clear_btn = QPushButton("Clear completed")
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

    def refresh(self, force=False):
        household = self.storage.get_household()
        configured = self.storage.sync_configured()
        signature = (
            configured,
            None if not household else (
                household.get("id"), household.get("invite_code"),
                tuple((m.get("name"), m.get("is_me")) for m in household.get("members", [])),
                tuple((t["id"], t["text"], t["done"], t.get("done_by_name")) for t in household.get("tasks", [])),
            ),
        )
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
            label = task["text"]
            if task["done"] and task.get("done_by_name"):
                label += f"   — ✓ {task['done_by_name']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if task["done"] else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
            if task["id"] == selected:
                self.list_widget.setCurrentItem(item)
        self.list_widget.blockSignals(False)
        self.list_widget.verticalScrollBar().setValue(scroll)

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
        if text and self.storage.shared_add_task(text):
            self.text_input.clear()
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
