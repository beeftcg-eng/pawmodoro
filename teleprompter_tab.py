"""
teleprompter_tab.py - The 🎬 Teleprompter tab: your scripts (a list on the
left, the text on the right, saved as you type), how the prompter should
look and move, and the button that opens it (teleprompter_window.py).

Scripts are plain text. A [cue in brackets] is shown in yellow on the
prompter and left out of the word count and reading time. Scripts stay on
this computer; they aren't part of Cloud Sync.
"""
import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

import teleprompter
import theme
from teleprompter_window import TeleprompterWindow

SAVE_DELAY_MS = 400


class TeleprompterTab(QWidget):
    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.prompter = None
        self.current_id = None

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self._save_text)

        outer = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_script_list())
        splitter.addWidget(self._build_editor())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 680])
        outer.addWidget(splitter, 1)
        outer.addLayout(self._build_settings_row())

        self.start_btn = QPushButton("▶  Start teleprompter")
        self.start_btn.setFont(theme.serif_font(13, bold=True))
        self.start_btn.setToolTip("Opens the prompter (F5). In it: Space starts and pauses, Esc closes.")
        self.start_btn.setShortcut("F5")
        self.start_btn.clicked.connect(self.start)
        outer.addWidget(self.start_btn)

        self._reload_list()
        self.refresh_theme()

    # ---------- Building ----------
    def _build_script_list(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Scripts")
        title.setFont(theme.serif_font(13, bold=True))
        layout.addWidget(title)
        self.script_list = QListWidget()
        self.script_list.currentItemChanged.connect(self._on_script_picked)
        self.script_list.itemDoubleClicked.connect(lambda item: self.rename_script())
        layout.addWidget(self.script_list, 1)
        buttons = QHBoxLayout()
        for text, tip, handler in (("New", "A new, empty script", self.new_script),
                                   ("Rename", "Rename this script", self.rename_script),
                                   ("Delete", "Delete this script", self.delete_script)):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        import_btn = QPushButton("Import a text file…")
        import_btn.clicked.connect(self.import_script)
        layout.addWidget(import_btn)
        return box

    def _build_editor(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "Write or paste your script here. It saves as you type.\n\n"
            "Put directions in [square brackets], like [smile] or [show the product]: "
            "they're shown in yellow on the prompter and don't count towards the reading time."
        )
        self.editor.setFont(theme.serif_font(12))
        self.editor.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.editor, 1)
        self.info_label = QLabel("")
        layout.addWidget(self.info_label)
        return box

    def _build_settings_row(self):
        s = self.storage.get_teleprompter_settings()
        row = QHBoxLayout()

        def spin(label, low, high, value, suffix, key, tip):
            row.addWidget(QLabel(label))
            box = QSpinBox()
            box.setRange(low, high)
            box.setValue(value)
            box.setSuffix(suffix)
            box.setToolTip(tip)
            box.valueChanged.connect(lambda v: self._set_setting(key, v))
            row.addWidget(box)
            return box

        self.wpm_box = spin("Speed", teleprompter.WPM_MIN, teleprompter.WPM_MAX, s["wpm"], " wpm", "wpm",
                            "Words per minute (about 130–160 is a natural speaking pace). ↑/↓ in the prompter.")
        self.wpm_box.setSingleStep(teleprompter.WPM_STEP)
        self.font_box = spin("Text", teleprompter.FONT_MIN, teleprompter.FONT_MAX, s["font_size"], " px",
                             "font_size", "Text size. +/− in the prompter.")
        self.font_box.setSingleStep(teleprompter.FONT_STEP)
        self.margin_box = spin("Sides", 0, teleprompter.MARGIN_MAX, s["margin"], " %", "margin",
                               "Empty space on each side. Wider margins keep your eyes nearer the camera.")
        self.countdown_box = spin("Countdown", 0, teleprompter.COUNTDOWN_MAX, s["countdown"], " s", "countdown",
                                  "A countdown before it starts scrolling from the top (0: none).")
        self.mirror_box = QCheckBox("Mirror")
        self.mirror_box.setToolTip("Flip left-right, for a glass (beam-splitter) teleprompter. M in the prompter.")
        self.mirror_box.setChecked(s["mirror"])
        self.mirror_box.toggled.connect(lambda v: self._set_setting("mirror", v))
        row.addWidget(self.mirror_box)
        self.fullscreen_box = QCheckBox("Full screen")
        self.fullscreen_box.setChecked(s["fullscreen"])
        self.fullscreen_box.toggled.connect(lambda v: self._set_setting("fullscreen", v))
        row.addWidget(self.fullscreen_box)
        row.addWidget(QLabel("on"))
        self.screen_box = QComboBox()
        self.screen_box.setToolTip("Which monitor the prompter opens on")
        self.screen_box.activated.connect(lambda i: self._set_setting("screen", self.screen_box.itemData(i)))
        row.addWidget(self.screen_box)
        row.addStretch()
        self._fill_screens()
        return row

    def _fill_screens(self):
        chosen = self.storage.get_teleprompter_settings()["screen"]
        self.screen_box.clear()
        self.screen_box.addItem("this window's monitor", None)
        for screen in QGuiApplication.screens():
            size = screen.geometry()
            self.screen_box.addItem(f"{screen.name()} ({size.width()}×{size.height()})", screen.name())
        index = self.screen_box.findData(chosen)
        self.screen_box.setCurrentIndex(max(0, index))

    def showEvent(self, event):
        self._fill_screens()  # monitors may have been plugged in or out
        super().showEvent(event)

    # ---------- Scripts ----------
    def _reload_list(self):
        self.script_list.blockSignals(True)
        self.script_list.clear()
        current = self.storage.get_current_teleprompter_script()
        for script in self.storage.get_teleprompter_scripts():
            item = QListWidgetItem(script["title"])
            item.setData(Qt.ItemDataRole.UserRole, script["id"])
            self.script_list.addItem(item)
            if script["id"] == current:
                self.script_list.setCurrentItem(item)
        self.script_list.blockSignals(False)
        self._show_script(current)

    def _show_script(self, script_id):
        self.current_id = script_id
        script = self.storage.get_teleprompter_script(script_id) if script_id else None
        self.editor.blockSignals(True)
        self.editor.setPlainText(script["text"] if script else "")
        self.editor.blockSignals(False)
        self._refresh_info()

    def _on_script_picked(self, item, previous):
        self._save_text()
        if item is not None:
            script_id = item.data(Qt.ItemDataRole.UserRole)
            self.storage.set_current_teleprompter_script(script_id)
            self._show_script(script_id)

    def new_script(self, title=None, text=""):
        self._save_text()
        if title is None:
            title, ok = QInputDialog.getText(self, "New script", "Name:",
                                             text=f"Script {len(self.storage.get_teleprompter_scripts()) + 1}")
            if not ok:
                return None
        script_id = self.storage.add_teleprompter_script(title, text)
        self._reload_list()
        self.editor.setFocus()
        return script_id

    def rename_script(self):
        script = self.storage.get_teleprompter_script(self.current_id) if self.current_id else None
        if script is None:
            return
        title, ok = QInputDialog.getText(self, "Rename script", "Name:", text=script["title"])
        if ok and title.strip():
            self.storage.set_teleprompter_script(self.current_id, title=title)
            self._reload_list()

    def delete_script(self):
        script = self.storage.get_teleprompter_script(self.current_id) if self.current_id else None
        if script is None:
            return
        answer = QMessageBox.question(self, "Delete script", f"Delete “{script['title']}”? This can't be undone.")
        if answer == QMessageBox.StandardButton.Yes:
            self.save_timer.stop()
            self.storage.remove_teleprompter_script(self.current_id)
            self._reload_list()

    def import_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import a script", "", "Text files (*.txt *.md);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except OSError as e:
            QMessageBox.warning(self, "Import a script", f"Couldn't read that file:\n{e}")
            return
        self.new_script(os.path.splitext(os.path.basename(path))[0], text)

    def _on_text_changed(self):
        if self.current_id is None:
            # Typing with no script yet makes one.
            self.current_id = self.storage.add_teleprompter_script("My script", "")
            self.script_list.blockSignals(True)
            item = QListWidgetItem("My script")
            item.setData(Qt.ItemDataRole.UserRole, self.current_id)
            self.script_list.addItem(item)
            self.script_list.setCurrentItem(item)
            self.script_list.blockSignals(False)
        self.save_timer.start(SAVE_DELAY_MS)
        self._refresh_info()

    def _save_text(self):
        self.save_timer.stop()
        if self.current_id is not None:
            script = self.storage.get_teleprompter_script(self.current_id)
            if script is not None and script["text"] != self.editor.toPlainText():
                self.storage.set_teleprompter_script(self.current_id, text=self.editor.toPlainText())

    # ---------- Settings / info ----------
    def _set_setting(self, key, value):
        self.storage.set_teleprompter_settings(**{key: value})
        self._refresh_info()

    def _refresh_info(self):
        text = self.editor.toPlainText()
        words = teleprompter.word_count(text)
        wpm = self.storage.get_teleprompter_settings()["wpm"]
        if words:
            self.info_label.setText(f"{words} words · about {teleprompter.format_duration(teleprompter.read_seconds(text, wpm))}"
                                    f" at {wpm} words a minute")
        else:
            self.info_label.setText("")
        self.start_btn.setEnabled(bool(words))

    def _refresh_settings(self):
        """Shows the settings the prompter may have changed (speed, size, mirror, full screen)."""
        s = self.storage.get_teleprompter_settings()
        for box, value in ((self.wpm_box, s["wpm"]), (self.font_box, s["font_size"])):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        for box, value in ((self.mirror_box, s["mirror"]), (self.fullscreen_box, s["fullscreen"])):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self._refresh_info()

    # ---------- The prompter ----------
    def start(self):
        self._save_text()
        text = self.editor.toPlainText()
        if not teleprompter.word_count(text):
            return
        if self.prompter is not None:
            self.prompter.close()
        script = self.storage.get_teleprompter_script(self.current_id)
        self.prompter = TeleprompterWindow(self.storage, text, script["title"] if script else "")
        self.prompter.closed.connect(self._on_prompter_closed)
        self.prompter.open_on(self.storage.get_teleprompter_settings()["screen"],
                              self.window().screen() if self.window() else None)

    def _on_prompter_closed(self):
        self.prompter = None
        self._refresh_settings()

    def close_prompter(self):
        if self.prompter is not None:
            self.prompter.close()

    def refresh_theme(self):
        self.info_label.setStyleSheet(f"color: {theme.current()['INK_SOFT']};")
