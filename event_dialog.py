"""
event_dialog.py - Adding or changing a calendar event (a trip, an
appointment, a booking): title, the day(s) it covers, an optional start and
end time, a note, whether it's on the household's calendar, and -- for a new
one -- files to attach once it's saved (tickets, reservations, receipts).

Also the small dialog for putting a task or a shared item on a day.
"""
import os
from datetime import date

from PyQt6.QtCore import QDate, QTime, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QTimeEdit, QVBoxLayout, QWidget
)


def _qdate(iso):
    return QDate.fromString(iso, "yyyy-MM-dd")


def _qtime(hm):
    return QTime.fromString(hm, "HH:mm")


class EventDialog(QDialog):
    """values() gives the fields for Storage.save_event; files() the paths
    to attach after saving (new events only)."""

    def __init__(self, day, event=None, shared=False, can_share=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit event" if event else "New event")
        self.setMinimumWidth(420)
        event = event or {}
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.title_edit = QLineEdit(event.get("title", ""))
        self.title_edit.setPlaceholderText("e.g. Trip to Lisbon, Dentist, Concert")
        self.title_edit.setMaxLength(200)
        form.addRow("What", self.title_edit)

        self.all_day = QCheckBox("All day")
        self.all_day.setChecked(not event.get("start_time"))
        self.all_day.toggled.connect(self._sync)
        form.addRow("", self.all_day)

        start_row = QHBoxLayout()
        self.start_date = QDateEdit(_qdate(event["start_date"]) if event.get("start_date") else _qdate(day.isoformat()))
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("ddd d MMM yyyy")
        self.start_date.dateChanged.connect(self._start_moved)
        start_row.addWidget(self.start_date)
        self.start_time = QTimeEdit(_qtime(event["start_time"]) if event.get("start_time") else QTime(9, 0))
        self.start_time.setDisplayFormat("HH:mm")
        start_row.addWidget(self.start_time)
        start_row.addStretch()
        form.addRow("From", start_row)

        end_row = QHBoxLayout()
        self.end_date = QDateEdit(_qdate(event.get("end_date") or event.get("start_date") or day.isoformat()))
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("ddd d MMM yyyy")
        end_row.addWidget(self.end_date)
        self.end_time_check = QCheckBox("at")
        self.end_time_check.setChecked(bool(event.get("end_time")))
        self.end_time_check.toggled.connect(self._sync)
        end_row.addWidget(self.end_time_check)
        self.end_time = QTimeEdit(_qtime(event["end_time"]) if event.get("end_time") else QTime(10, 0))
        self.end_time.setDisplayFormat("HH:mm")
        end_row.addWidget(self.end_time)
        end_row.addStretch()
        form.addRow("Until", end_row)

        self.note_edit = QPlainTextEdit(event.get("note", ""))
        self.note_edit.setPlaceholderText("Booking number, address, flight, who's coming…")
        self.note_edit.setFixedHeight(80)
        form.addRow("Note", self.note_edit)

        self.share_check = QCheckBox("\U0001F3E0 On the household's calendar")
        self.share_check.setChecked(shared)
        # only whoever added a household event can take it back off (save_event in schema.sql)
        locked = shared and not event.get("created_by_me", True)
        self.share_check.setEnabled(can_share and not locked)
        self.share_check.setToolTip(
            "Only whoever added it can take it off the household's calendar" if locked
            else "Everyone in your household sees it (and its files)" if can_share
            else "Create or join a household on the Shared tab first")
        form.addRow("", self.share_check)
        layout.addLayout(form)

        self._files = []
        self.files_box = QWidget()
        if not event:  # an existing event's files are managed in the Calendar tab
            files_layout = QVBoxLayout(self.files_box)
            files_layout.setContentsMargins(0, 0, 0, 0)
            files_row = QHBoxLayout()
            files_row.addWidget(QLabel("\U0001F4CE Files (tickets, reservations, receipts):"))
            files_row.addStretch()
            add_files = QPushButton("Add files…")
            add_files.clicked.connect(self._pick_files)
            files_row.addWidget(add_files)
            files_layout.addLayout(files_row)
            self.files_list = QListWidget()
            self.files_list.setFixedHeight(70)
            files_layout.addWidget(self.files_list)
            layout.addWidget(self.files_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._sync()
        self.title_edit.setFocus()

    def _sync(self, _checked=None):
        timed = not self.all_day.isChecked()
        self.start_time.setVisible(timed)
        self.end_time_check.setVisible(timed)
        self.end_time.setVisible(timed)
        self.end_time.setEnabled(self.end_time_check.isChecked())

    def _start_moved(self, new_start):
        if self.end_date.date() < new_start:
            self.end_date.setDate(new_start)

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        for path in paths:
            if path not in self._files:
                self._files.append(path)
                self.files_list.addItem(os.path.basename(path))

    def _accept(self):
        if not self.title_edit.text().strip():
            QMessageBox.information(self, "Event", "Give the event a title.")
            return
        values = self.values()
        if values["end_date"] < values["start_date"] or (
                values["end_date"] == values["start_date"] and values["end_time"]
                and values["end_time"] < values["start_time"]):
            QMessageBox.information(self, "Event", "It ends before it starts.")
            return
        self.accept()

    def values(self):
        timed = not self.all_day.isChecked()
        return {
            "title": self.title_edit.text().strip(),
            "start_date": self.start_date.date().toString("yyyy-MM-dd"),
            "end_date": self.end_date.date().toString("yyyy-MM-dd"),
            "start_time": self.start_time.time().toString("HH:mm") if timed else None,
            "end_time": self.end_time.time().toString("HH:mm") if timed and self.end_time_check.isChecked() else None,
            "note": self.note_edit.toPlainText().strip(),
            "shared": self.share_check.isChecked(),
        }

    def files(self):
        return list(self._files)


class DayItemDialog(QDialog):
    """Text and an optional time for a task / shared item on one day."""

    def __init__(self, title, day, placeholder, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        heading = QLabel(day.strftime("%A %d %B %Y") if day.year != date.today().year else day.strftime("%A %d %B"))
        heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(heading)
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText(placeholder)
        layout.addWidget(self.text_edit)
        time_row = QHBoxLayout()
        self.time_check = QCheckBox("At")
        self.time_check.setToolTip("A time of day (you get a reminder then)")
        time_row.addWidget(self.time_check)
        self.time_edit = QTimeEdit(QTime(9, 0))
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setEnabled(False)
        self.time_check.toggled.connect(self.time_edit.setEnabled)
        time_row.addWidget(self.time_edit)
        time_row.addStretch()
        layout.addLayout(time_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(lambda: self.text_edit.text().strip() and self.accept())
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self):
        return self.text_edit.text().strip()

    def time(self):
        return self.time_edit.time().toString("HH:mm") if self.time_check.isChecked() else None
