"""
calendar_tab.py - The "Calendar" tab: a month grid of what's on each day --
your dated tasks (one-offs with a due date, monthly ones, specific-day ones),
the shared list's scheduled items, and the calendar's own events (trips,
appointments, bookings) -- and, for the selected day, a list of them with
their details and attached files.

Files (tickets, reservations, receipts, photos) can go on any of them: pick
them with "Attach files…" or drop them on the files list. They're copied into
the app's data folder and, with Cloud Sync, uploaded so your phone (and, on
shared items and household events, the rest of your household) can open
them. A file added on another device is downloaded the first time it's
opened here.

What goes on which day is calendar_view.py; the data is Storage's.
"""
import calendar
import os
import shutil
import threading
from datetime import date, timedelta

from PyQt6.QtCore import QRectF, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget
)

import calendar_view
import theme
from event_dialog import DayItemDialog, EventDialog
from storage import attachment_file
from supabase_sync import SyncError

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
KIND_ICONS = {"event": "\U0001F4C5", "task": "✅", "shared": "\U0001F3E0"}
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp")
DONE_ROLE = Qt.ItemDataRole.UserRole + 1


def _blend(a, b, amount):
    """Colour `a` moved `amount` (0..1) of the way to `b`."""
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * amount), round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount))


class MonthGrid(QWidget):
    """A month, one cell per day, with what's on each day written in it."""

    day_selected = pyqtSignal(object)  # date
    day_activated = pyqtSignal(object)  # date (double-click: add an event there)
    month_changed = pyqtSignal(int, int)  # year, month (moved with the keyboard)

    HEADER = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        today = date.today()
        self.year, self.month = today.year, today.month
        self.selected = today
        self.entries = {}  # date -> entries (calendar_view.month_entries)
        self.setToolTip("Click a day to see it; double-click to add an event. Arrow keys move.")

    def set_month(self, year, month, entries):
        self.year, self.month, self.entries = year, month, entries
        self.update()

    def _rows(self):
        return calendar_view.week_rows(self.year, self.month)

    def _cell_rect(self, row, col, rows):
        w = self.width() / 7
        h = (self.height() - self.HEADER) / rows
        return QRectF(col * w, self.HEADER + row * h, w, h)

    def _day_at(self, pos):
        rows = self._rows()
        col = int(pos.x() // (self.width() / 7))
        row = int((pos.y() - self.HEADER) // ((self.height() - self.HEADER) / len(rows)))
        if pos.y() < self.HEADER or not (0 <= row < len(rows) and 0 <= col < 7):
            return None
        return rows[row][col]

    def mousePressEvent(self, event):
        day = self._day_at(event.position())
        if day is not None:
            self.select(day)

    def mouseDoubleClickEvent(self, event):
        day = self._day_at(event.position())
        if day is not None:
            self.day_activated.emit(day)

    def keyPressEvent(self, event):
        steps = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -7, Qt.Key.Key_Down: 7}
        if event.key() in steps:
            self.select(self.selected + timedelta(days=steps[event.key()]))
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.day_activated.emit(self.selected)
        else:
            super().keyPressEvent(event)

    def select(self, day):
        self.selected = day
        if (day.year, day.month) != (self.year, self.month):
            self.month_changed.emit(day.year, day.month)
        self.update()
        self.day_selected.emit(day)

    def paintEvent(self, _event):
        p = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(theme.serif_font(9))
        small = QFont(theme.serif_font(8))
        painter.setFont(font)
        metrics = QFontMetrics(small)
        rows = self._rows()
        w = self.width() / 7
        today = date.today()

        painter.setPen(QColor(p["INK_SOFT"]))
        for col, name in enumerate(WEEKDAYS):
            painter.drawText(QRectF(col * w, 0, w, self.HEADER), Qt.AlignmentFlag.AlignCenter, name)

        chip_colors = {"event": QColor(p["ACCENT"]), "shared": QColor(p["INK_SOFT"]), "task": QColor(p["PAPER_EDGE"])}
        for r, week in enumerate(rows):
            for c, day in enumerate(week):
                rect = self._cell_rect(r, c, len(rows)).adjusted(1, 1, -1, -1)
                in_month = day.month == self.month
                fill = QColor(p["PAPER_LIGHT"]) if in_month else _blend(p["PAPER"], p["PAPER_EDGE"], 0.15)
                if day == self.selected:
                    fill = _blend(p["PAPER_LIGHT"], p["ACCENT_SOFT"], 0.45)
                painter.setPen(QPen(QColor(p["PAPER_EDGE"]), 1))
                painter.setBrush(fill)
                painter.drawRect(rect)
                if day == today:
                    painter.setPen(QPen(QColor(p["ACCENT"]), 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(rect.adjusted(1, 1, -1, -1))

                painter.setFont(font)
                painter.setPen(QColor(p["INK"] if in_month else p["INK_SOFT"]))
                painter.drawText(rect.adjusted(4, 2, -4, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                                 str(day.day))

                entries = self.entries.get(day, []) if in_month else []
                if not entries:
                    continue
                painter.setFont(small)
                line = metrics.height() + 2
                top = rect.top() + QFontMetrics(font).height() + 4
                room = max(0, int((rect.bottom() - top - 2) // line))
                shown = entries if len(entries) <= room else entries[:max(0, room - 1)]
                for i, entry in enumerate(shown):
                    chip = QRectF(rect.left() + 3, top + i * line, rect.width() - 6, line - 1)
                    color = QColor(chip_colors[entry["kind"]])
                    if entry["done"]:
                        color = _blend(color.name(), p["PAPER_LIGHT"], 0.6)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(color)
                    painter.drawRoundedRect(chip, 3, 3)
                    text_color = QColor(p["PAPER_LIGHT"]) if entry["kind"] != "task" else QColor(p["INK"])
                    painter.setPen(text_color)
                    label = (f"{entry['time']} " if entry["time"] else "") + entry["title"]
                    if entry["span"] in ("middle", "end"):
                        label = "… " + entry["title"]
                    item_font = QFont(small)
                    item_font.setStrikeOut(entry["done"])
                    painter.setFont(item_font)
                    painter.drawText(chip.adjusted(4, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter,
                                     metrics.elidedText(label, Qt.TextElideMode.ElideRight, int(chip.width()) - 6))
                if len(shown) < len(entries):
                    painter.setFont(small)
                    painter.setPen(QColor(p["INK_SOFT"]))
                    painter.drawText(QRectF(rect.left() + 4, top + len(shown) * line, rect.width() - 8, line),
                                     Qt.AlignmentFlag.AlignVCenter, f"+{len(entries) - len(shown)} more")
        painter.end()


class FileList(QListWidget):
    """The selected item's files; files dropped on it are attached."""

    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setIconSize(QSize(40, 40))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.isfile(p)]
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)


class CalendarTab(QWidget):
    # a task or shared item was added here (the Checklist / Shared tabs refresh)
    items_changed = pyqtSignal()
    open_task = pyqtSignal(str)
    open_shared = pyqtSignal()
    # (attachment id, local file or "", error text or "") from a download thread
    _downloaded = pyqtSignal(str, str, str)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self._selected_entry = None  # (kind, id)
        self._downloading = set()
        self._pending_action = None  # (attachment id, what to do with it once downloaded)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        nav = QHBoxLayout()
        prev_btn = QPushButton("◀")
        prev_btn.setToolTip("Previous month")
        prev_btn.clicked.connect(lambda: self._step_month(-1))
        nav.addWidget(prev_btn)
        self.month_label = QLabel("")
        self.month_label.setFont(theme.serif_font(13, bold=True))
        nav.addWidget(self.month_label)
        next_btn = QPushButton("▶")
        next_btn.setToolTip("Next month")
        next_btn.clicked.connect(lambda: self._step_month(1))
        nav.addWidget(next_btn)
        today_btn = QPushButton("Today")
        today_btn.clicked.connect(self.go_to_today)
        nav.addWidget(today_btn)
        nav.addStretch()
        self.add_btn = QPushButton("+ Add")
        add_menu = QMenu(self)
        add_menu.addAction("\U0001F4C5 Event (trip, appointment…)…", lambda: self.add_event())
        add_menu.addAction("✅ Task on this day…", self.add_task)
        add_menu.addAction("\U0001F3E0 Shared item on this day…", self.add_shared)
        self.add_btn.setMenu(add_menu)
        nav.addWidget(self.add_btn)
        outer.addLayout(nav)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.grid = MonthGrid()
        self.grid.day_selected.connect(self._on_day_selected)
        self.grid.day_activated.connect(lambda day: self.add_event(day))
        self.grid.month_changed.connect(lambda y, m: self.show_month(y, m))
        splitter.addWidget(self.grid)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 0, 0, 0)
        self.day_label = QLabel("")
        self.day_label.setFont(theme.serif_font(11, bold=True))
        side_layout.addWidget(self.day_label)
        self.day_list = QListWidget()
        self.day_list.currentItemChanged.connect(self._on_entry_selected)
        self.day_list.itemDoubleClicked.connect(lambda _item: self.edit_selected())
        side_layout.addWidget(self.day_list, 2)

        self.details_label = QLabel("")
        self.details_label.setWordWrap(True)
        self.details_label.setTextFormat(Qt.TextFormat.PlainText)  # titles and notes may come from others
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        side_layout.addWidget(self.details_label)
        item_buttons = QHBoxLayout()
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.clicked.connect(self.edit_selected)
        item_buttons.addWidget(self.edit_btn)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self.delete_selected)
        item_buttons.addWidget(self.delete_btn)
        item_buttons.addStretch()
        side_layout.addLayout(item_buttons)

        self.files_label = QLabel("\U0001F4CE Files")
        side_layout.addWidget(self.files_label)
        self.file_list = FileList()
        self.file_list.setToolTip("Drop files here to attach them. Double-click one to open it.")
        self.file_list.files_dropped.connect(self.attach_paths)
        self.file_list.itemDoubleClicked.connect(lambda _item: self.open_selected_file())
        side_layout.addWidget(self.file_list, 1)
        file_buttons = QHBoxLayout()
        self.attach_btn = QPushButton("Attach files…")
        self.attach_btn.clicked.connect(self.attach_files)
        file_buttons.addWidget(self.attach_btn)
        self.open_file_btn = QPushButton("Open")
        self.open_file_btn.clicked.connect(self.open_selected_file)
        file_buttons.addWidget(self.open_file_btn)
        self.save_file_btn = QPushButton("Save a copy…")
        self.save_file_btn.clicked.connect(self.save_selected_file)
        file_buttons.addWidget(self.save_file_btn)
        self.remove_file_btn = QPushButton("Remove")
        self.remove_file_btn.clicked.connect(self.remove_selected_file)
        file_buttons.addWidget(self.remove_file_btn)
        side_layout.addLayout(file_buttons)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        side_layout.addWidget(self.status_label)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

        self._downloaded.connect(self._on_downloaded)
        self.refresh_theme()
        self.go_to_today()

    # ---------- month / day ----------

    def go_to_today(self):
        today = date.today()
        self.show_month(today.year, today.month)
        self.grid.select(today)

    def _step_month(self, delta):
        month = self.grid.month + delta
        year = self.grid.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        self.show_month(year, month)
        # keep the same day of the month selected (or the last day there is)
        day = min(self.grid.selected.day, calendar.monthrange(year, month)[1])
        self.grid.select(date(year, month, day))

    def show_month(self, year, month):
        entries = calendar_view.month_entries(year, month, self.storage.get_checklist(), self.storage.get_household(),
                                              self.storage.get_events())
        self.grid.set_month(year, month, entries)
        self.month_label.setText(date(year, month, 1).strftime("%B %Y"))

    def refresh(self):
        """Data changed (a pull, another tab, a new day): redraw."""
        self.show_month(self.grid.year, self.grid.month)
        self._fill_day(self.grid.selected)

    def _on_day_selected(self, day):
        self._selected_entry = None
        self._fill_day(day)

    def _fill_day(self, day):
        today = date.today()
        self.day_label.setText(day.strftime("%A %d %B" if day.year == today.year else "%A %d %B %Y")
                               + ("  (today)" if day == today else ""))
        entries = calendar_view.entries_on(day, self.storage.get_checklist(), self.storage.get_household(),
                                           self.storage.get_events())
        keep = self._selected_entry
        self.day_list.blockSignals(True)
        self.day_list.clear()
        for entry in entries:
            text = f"{KIND_ICONS[entry['kind']]} " + (f"{entry['time']}  " if entry["time"] else "") + entry["title"]
            if entry["kind"] == "event" and entry["shared"]:
                text += "  (household)"
            if entry["done"]:
                text += "  ✓"
            count = len(self.storage.attachments_for(entry["kind"], entry["id"]))
            if count:
                text += f"  \U0001F4CE{count}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, (entry["kind"], entry["id"]))
            item.setData(DONE_ROLE, entry["done"])
            if entry["done"]:
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
            self.day_list.addItem(item)
            if (entry["kind"], entry["id"]) == keep:
                self.day_list.setCurrentItem(item)
        if self.day_list.currentItem() is None and self.day_list.count():
            self.day_list.setCurrentRow(0)
        self.day_list.blockSignals(False)
        if not entries:
            placeholder = QListWidgetItem("Nothing on this day. Double-click it in the calendar, or + Add.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.day_list.addItem(placeholder)
        self._on_entry_selected(self.day_list.currentItem())

    # ---------- the selected entry ----------

    def _current(self):
        """(kind, id, the item's dict, shared) for the selected entry, or None."""
        item = self.day_list.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not key:
            return None
        kind, item_id = key
        if kind == "event":
            event, shared = self.storage.find_event(item_id)
            return (kind, item_id, event, shared) if event else None
        if kind == "task":
            task = next((t for t in self.storage.get_checklist() if t["id"] == item_id), None)
            return (kind, item_id, task, False) if task else None
        task = next((t for t in (self.storage.get_household() or {}).get("tasks", []) if t["id"] == item_id), None)
        return (kind, item_id, task, True) if task else None

    def _on_entry_selected(self, item, _previous=None):
        current = self._current() if item else None
        self._selected_entry = current[:2] if current else None
        has = current is not None
        for widget in (self.edit_btn, self.delete_btn, self.files_label, self.file_list, self.attach_btn,
                       self.open_file_btn, self.save_file_btn, self.remove_file_btn):
            widget.setVisible(has)
        if not has:
            self.details_label.setText("")
            self.status_label.setText("")
            return
        kind, _item_id, data, shared = current
        if kind == "event":
            lines = [calendar_view.describe_event(data)]
            if shared:
                by = "you" if data.get("created_by_me", True) else (data.get("created_by_name") or "someone")
                lines.append(f"\U0001F3E0 On the household's calendar (added by {by})")
            if data.get("note"):
                lines.append(data["note"])
            self.edit_btn.setText("Edit…")
            self.delete_btn.setVisible(True)
        else:
            lines = [calendar_view.item_label(kind) + (" — done" if item.data(DONE_ROLE) else "")]
            if kind == "task" and data.get("note"):
                lines.append(data["note"])
            if kind == "shared" and data.get("assigned_to"):
                lines.append("→ " + ("you" if data.get("assigned_to_me") else data.get("assigned_name") or "?"))
            self.edit_btn.setText("Open in Checklist" if kind == "task" else "Open in Shared")
            self.delete_btn.setVisible(False)
        self.details_label.setText("\n".join(lines))
        self._fill_files()

    def edit_selected(self):
        current = self._current()
        if current is None:
            return
        kind, item_id, data, shared = current
        if kind == "task":
            self.open_task.emit(item_id)
        elif kind == "shared":
            self.open_shared.emit()
        else:
            self.add_event(event=data, shared=shared)

    def delete_selected(self):
        current = self._current()
        if current is None or current[0] != "event":
            return
        _kind, item_id, event, shared = current
        files = len(self.storage.attachments_for("event", item_id))
        extra = f" and its {files} file{'s' if files != 1 else ''}" if files else ""
        whose = " It's removed for everyone in your household." if shared else ""
        if QMessageBox.question(self, "Delete event", f"Delete “{event['title']}”{extra}?{whose}") \
                != QMessageBox.StandardButton.Yes:
            return
        self.storage.remove_event(item_id)
        self.refresh()

    # ---------- adding ----------

    def add_event(self, day=None, event=None, shared=False):
        day = day or self.grid.selected
        dialog = EventDialog(day, event, shared, can_share=self.storage.get_household() is not None, parent=self)
        if not dialog.exec():
            return
        values = dialog.values()
        try:
            saved = self.storage.save_event(event_id=event["id"] if event else None, **values)
        except ValueError as e:
            QMessageBox.information(self, "Event", str(e))
            return
        self._selected_entry = ("event", saved["id"])
        self._attach(("event", saved["id"]), dialog.files())
        start = date.fromisoformat(saved["start_date"])
        self.show_month(start.year, start.month)
        self.grid.select(start)

    def add_task(self):
        day = self.grid.selected
        dialog = DayItemDialog("New task", day, "e.g. Renew passport", self)
        if not dialog.exec():
            return
        task = self.storage.add_task(dialog.text(), "once", reminder_time=dialog.time(), due_date=day.isoformat())
        self._selected_entry = ("task", task["id"])
        self.items_changed.emit()
        self.refresh()

    def add_shared(self):
        if not self.storage.get_household():
            QMessageBox.information(self, "Shared item", "Create or join a household on the Shared tab first.")
            return
        day = self.grid.selected
        dialog = DayItemDialog("New shared item", day, "e.g. Pick up the keys", self)
        if not dialog.exec():
            return
        task = self.storage.shared_add_task(dialog.text(), {"recurrence": "once", "due_date": day.isoformat(),
                                                            "due_time": dialog.time()})
        if task:
            self._selected_entry = ("shared", task["id"])
        self.items_changed.emit()
        self.refresh()

    # ---------- files ----------

    def _fill_files(self):
        current = self._current()
        self.file_list.clear()
        if current is None:
            return
        attachments = self.storage.attachments_for(current[0], current[1])
        for attachment in attachments:
            local = self.storage.attachment_local_path(attachment)
            text = f"{attachment['file_name']}  ({calendar_view.human_size(attachment.get('size'))})"
            if not attachment.get("added_by_me", True) and attachment.get("added_by_name"):
                text += f" — {attachment['added_by_name']}"
            if attachment.get("upload_error"):
                text += f"\n⚠ Not uploaded: {attachment['upload_error']}"
            elif not attachment.get("uploaded") and self.storage.sync_configured():
                text += "  ☁ uploading…"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, attachment["id"])
            if local and attachment.get("mime") in IMAGE_TYPES:
                pixmap = QPixmap(local)
                if not pixmap.isNull():
                    item.setIcon(QIcon(pixmap.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation)))
            self.file_list.addItem(item)
        if not attachments:
            hint = QListWidgetItem("No files yet. Drop some here, or Attach files…")
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            self.file_list.addItem(hint)
        shared = current[3]
        if shared:
            self.status_label.setText("Files here are shared with your household.")
        elif not self.storage.sync_configured():
            self.status_label.setText("Files stay on this computer until Cloud Sync is on.")
        else:
            self.status_label.setText("")

    def attach_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        self.attach_paths(paths)

    def attach_paths(self, paths):
        current = self._current()
        if current is not None:
            self._attach(current[:2], paths)

    def _attach(self, key, paths):
        problems = []
        for path in paths:
            try:
                self.storage.add_attachment(key[0], key[1], path)
            except (ValueError, OSError) as e:
                problems.append(f"{os.path.basename(path)}: {e}")
        if problems:
            QMessageBox.warning(self, "Attach files", "Couldn't attach:\n" + "\n".join(problems))
        self.refresh()

    def _selected_attachment(self):
        item = self.file_list.currentItem()
        attachment_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return self.storage.find_attachment(attachment_id) if attachment_id else None

    def open_selected_file(self):
        self._with_local_file(lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))

    def save_selected_file(self):
        def save(path):
            target, _ = QFileDialog.getSaveFileName(self, "Save a copy", os.path.join(
                os.path.expanduser("~"), os.path.basename(path)))
            if target:
                try:
                    shutil.copyfile(path, target)
                except OSError as e:
                    QMessageBox.warning(self, "Save a copy", f"Couldn't save it: {e}")
        self._with_local_file(save)

    def _with_local_file(self, action):
        """Runs action(path) on the selected file, downloading it first if
        it came from another device."""
        attachment = self._selected_attachment()
        if attachment is None:
            return
        local = self.storage.attachment_local_path(attachment)
        if local:
            action(local)
            return
        if not attachment.get("path"):
            QMessageBox.information(self, "Files", "This file isn't on this computer and hasn't reached the cloud.")
            return
        self._pending_action = (attachment["id"], action)
        if attachment["id"] in self._downloading:
            return
        self._downloading.add(attachment["id"])
        self.status_label.setText(f"Downloading {attachment['file_name']}…")
        destination = attachment_file(attachment["id"], attachment["file_name"])

        def work():
            try:
                self.storage.sync.download(attachment["path"], destination)
                self._downloaded.emit(attachment["id"], destination, "")
            except (SyncError, OSError) as e:
                self._downloaded.emit(attachment["id"], "", str(e))

        threading.Thread(target=work, name="pawmodoro-download", daemon=True).start()

    def _on_downloaded(self, attachment_id, path, error):
        self._downloading.discard(attachment_id)
        if error:
            self.status_label.setText("")
            QMessageBox.warning(self, "Files", f"Couldn't download it: {error}")
            return
        self.status_label.setText("")
        self._fill_files()
        pending = self._pending_action
        if pending and pending[0] == attachment_id:
            self._pending_action = None
            pending[1](path)

    def remove_selected_file(self):
        attachment = self._selected_attachment()
        if attachment is None:
            return
        shared = self._current() and self._current()[3]
        whose = " It's removed for everyone in your household." if shared else ""
        if QMessageBox.question(self, "Remove file", f"Remove “{attachment['file_name']}”?{whose}") \
                != QMessageBox.StandardButton.Yes:
            return
        self.storage.remove_attachment(attachment["id"])
        self.refresh()

    # ---------- theme ----------

    def refresh_theme(self):
        p = theme.current()
        self.month_label.setStyleSheet(f"color: {p['INK']};")
        self.day_label.setStyleSheet(f"color: {p['INK']};")
        self.details_label.setStyleSheet(f"color: {p['INK']};")
        self.status_label.setStyleSheet(f"color: {p['INK_SOFT']};")
        self.files_label.setStyleSheet(f"color: {p['INK_SOFT']};")
        self.grid.update()
