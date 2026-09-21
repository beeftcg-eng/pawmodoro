"""
pomodoro_tab.py - Configurable pomodoro timer with work/short-break/long-break
cycling and desktop notifications when a phase ends.
"""
import math
import os
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QFormLayout, QGroupBox, QSlider, QCheckBox, QFileDialog, QInputDialog,
    QScrollArea
)
from PyQt6.QtCore import QTimer, Qt, QEvent, pyqtSignal

import theme
from theme import serif_font
from circular_timer import CircularTimer
from ambient_loop import AmbientLoop, AMBIENT_SOUNDS, MAX_CONCURRENT
import chime
import notifier
import quotes

AUDIO_FILE_FILTER = "Audio files (*.wav *.wave *.mp3 *.ogg *.flac *.m4a *.aac);;All files (*)"

# Debounce window for ambient volume slider drags: CLI ambient players can
# only change volume by restarting the underlying process, so applying it
# on every valueChanged() tick (dozens of times per drag) causes audible
# stutter and needless process churn. Only the value the slider settles on
# actually gets applied.
VOLUME_APPLY_DELAY_MS = 200

# The countdown is measured against an end time rather than by counting
# ticks, so it can't drift. If more than this many seconds pass between two
# ticks the computer was asleep or the app was frozen; that gap isn't counted
# (the timer behaves as if paused), so closing the laptop lid mid-session
# doesn't finish -- and credit -- a pomodoro.
STALL_GAP_SECONDS = 3
TICK_MS = 250

PHASE_NAMES = {
    "work": "\U0001F43E Work session",
    "short_break": "☕ Short break",
    "long_break": "\U0001F319 Long break",
}


def notify(title, message):
    notifier.send(title, message)


class _StableScrollArea(QScrollArea):
    """A QScrollArea that doesn't jump on its own. QScrollArea's default
    behavior is to auto-scroll to whichever child widget receives keyboard
    focus — including whatever Qt happens to pick as the initial focus
    target the moment this tab first becomes visible, which visually
    looked like the ring scrolling itself out of view for no reason.
    Skipping just that one auto-scroll (the focused widget still gets the
    focus-in event normally) keeps scrolling-to-a-clicked-field working
    while removing the surprise jump."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FocusIn:
            return False
        return super().eventFilter(obj, event)


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
        else:
            sub_layout = item.layout()
            if sub_layout is not None:
                _clear_layout(sub_layout)


class PomodoroTab(QWidget):
    # emitted so widget-mode window can mirror the countdown
    # (time_text, phase_text, fraction_elapsed 0..1, phase_key)
    tick = pyqtSignal(str, str, float, str)
    # emitted for anything worth celebrating in-app (title, detail)
    celebrate = pyqtSignal(str, str)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        settings = storage.get_pomodoro_settings()

        self.phase = "work"  # work / short_break / long_break
        self.sessions_completed = 0
        self.seconds_left = settings["work_min"] * 60
        self.running = False
        self._deadline = 0.0
        self._last_tick = 0.0
        self._ambient_auto_changing = False  # True while auto-mode flips the checkboxes itself

        outer = QVBoxLayout(self)

        scroll = _StableScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(14)

        # --- Ring timer + session progress dots ---
        self.circular_timer = CircularTimer(min_side=150)
        self.circular_timer.setFixedSize(220, 220)
        layout.addWidget(self.circular_timer, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.session_dots_label = QLabel("")
        self.session_dots_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.session_dots_label.setFont(serif_font(13))
        layout.addWidget(self.session_dots_label)

        # --- Transport: a prominent Start/Pause pill, small secondary actions ---
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        reset_btn = QPushButton("↺ Reset")
        reset_btn.setObjectName("secondary_btn")
        reset_btn.clicked.connect(self.reset_phase)
        btn_row.addWidget(reset_btn)

        self.start_btn = QPushButton("▶  Start")
        self.start_btn.setObjectName("primary_btn")
        self.start_btn.clicked.connect(self.toggle_running)
        btn_row.addWidget(self.start_btn)

        skip_btn = QPushButton("Skip ⏭")
        skip_btn.setObjectName("secondary_btn")
        skip_btn.setToolTip("Jump to the next phase. Skipping doesn't earn XP or count as a finished session.")
        skip_btn.clicked.connect(lambda: self.advance_phase(completed=False))
        btn_row.addWidget(skip_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Settings group
        settings_box = QGroupBox("Timer settings (minutes)")
        form = QFormLayout(settings_box)

        self.work_spin = QSpinBox()
        self.work_spin.setRange(1, 180)
        self.work_spin.setValue(settings["work_min"])
        form.addRow("Work:", self.work_spin)

        self.short_break_spin = QSpinBox()
        self.short_break_spin.setRange(1, 60)
        self.short_break_spin.setValue(settings["short_break_min"])
        form.addRow("Short break:", self.short_break_spin)

        self.long_break_spin = QSpinBox()
        self.long_break_spin.setRange(1, 120)
        self.long_break_spin.setValue(settings["long_break_min"])
        form.addRow("Long break:", self.long_break_spin)

        self.sessions_spin = QSpinBox()
        self.sessions_spin.setRange(1, 12)
        self.sessions_spin.setValue(settings["sessions_before_long_break"])
        form.addRow("Sessions before long break:", self.sessions_spin)

        chime_row = QHBoxLayout()
        self.chime_check = QCheckBox("Play a chime when a session ends")
        self.chime_check.setChecked(settings.get("chime", True))
        chime_row.addWidget(self.chime_check)
        test_chime_btn = QPushButton("\U0001F514 Test")
        test_chime_btn.clicked.connect(chime.play_chime)
        chime_row.addWidget(test_chime_btn)
        chime_row.addStretch()
        form.addRow(chime_row)

        self.auto_next_check = QCheckBox("Start the next session or break automatically")
        self.auto_next_check.setChecked(settings.get("auto_start_next", True))
        form.addRow(self.auto_next_check)

        self.ambient_auto_check = QCheckBox("Play my ambient sounds only during work sessions")
        self.ambient_auto_check.setToolTip(
            "Starts the ambient mix you last picked when a work session starts, "
            "and stops it for breaks."
        )
        self.ambient_auto_check.setChecked(settings.get("ambient_auto", False))
        form.addRow(self.ambient_auto_check)

        save_settings_btn = QPushButton("Save settings")
        save_settings_btn.clicked.connect(self.save_settings)
        form.addRow(save_settings_btn)

        layout.addWidget(settings_box)

        # --- Ambient sounds, for focus: pick up to 3 to play together ---
        ambient_box = QGroupBox(f"Ambient sounds (up to {MAX_CONCURRENT} at once)")
        ambient_box_layout = QVBoxLayout(ambient_box)

        # A scroll area (rather than adding rows straight to the box) means
        # the number of ambient sounds — which grows as you add your own —
        # doesn't dictate the window's minimum height. Without this, the
        # window couldn't be resized smaller than "tall enough to show
        # every row at once".
        ambient_scroll = QScrollArea()
        ambient_scroll.setWidgetResizable(True)
        ambient_scroll.setMaximumHeight(200)
        ambient_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        ambient_rows_container = QWidget()
        self.ambient_rows_layout = QVBoxLayout(ambient_rows_container)
        self.ambient_rows_layout.setContentsMargins(0, 0, 0, 0)
        ambient_scroll.setWidget(ambient_rows_container)

        ambient_box_layout.addWidget(ambient_scroll)

        self.ambient_status_label = QLabel("")
        self.ambient_status_label.setObjectName("ambient_status_label")
        ambient_box_layout.addWidget(self.ambient_status_label)

        add_row = QHBoxLayout()
        add_sound_btn = QPushButton("➕ Add your own sound…")
        add_sound_btn.clicked.connect(self._add_custom_sound)
        add_row.addWidget(add_sound_btn)

        self.restore_sounds_btn = QPushButton("Restore removed default sounds")
        self.restore_sounds_btn.clicked.connect(self._restore_builtin_sounds)
        add_row.addWidget(self.restore_sounds_btn)
        ambient_box_layout.addLayout(add_row)

        self.ambient_loops = {}
        self.ambient_checks = {}
        self.ambient_sliders = {}
        self._volume_debounce_timers = {}
        self._pending_ambient_volume = {}
        self._build_ambient_rows()

        layout.addWidget(ambient_box)
        layout.addStretch()

        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._on_tick)

        self.refresh_theme()
        self._refresh_labels()

    def _build_ambient_rows(self):
        """(Re)builds the ambient sound checkbox/slider rows from the
        built-in sounds (minus any the user has hidden) plus whatever
        custom sounds are in storage."""
        # stop anything currently playing before we tear down the widgets
        for loop in self.ambient_loops.values():
            loop.stop()
        for timer in self._volume_debounce_timers.values():
            timer.stop()

        _clear_layout(self.ambient_rows_layout)
        self.ambient_loops = {}
        self.ambient_checks = {}
        self.ambient_sliders = {}
        self._volume_debounce_timers = {}
        self._pending_ambient_volume = {}

        hidden = set(self.storage.get_hidden_builtin_sounds())
        for key, (label, _filename) in AMBIENT_SOUNDS.items():
            if key in hidden:
                continue
            loop = AmbientLoop(sound_key=key)
            self._add_ambient_row(key, label, loop, removable=True, is_builtin=True)

        for sound in self.storage.get_custom_sounds():
            key = f"custom:{sound['id']}"
            loop = AmbientLoop(label=sound["label"], path=sound["path"])
            self._add_ambient_row(key, sound["label"], loop, removable=True,
                                   is_builtin=False, custom_id=sound["id"])

        self.restore_sounds_btn.setVisible(bool(hidden))

    def _add_ambient_row(self, key, label, loop, removable, is_builtin=False, custom_id=None):
        row = QHBoxLayout()

        check = QCheckBox(label)
        check.toggled.connect(lambda checked, k=key: self._toggle_ambient(k, checked))
        row.addWidget(check)

        row.addWidget(QLabel("Volume"))
        volume = self.storage.get_ambient_prefs().get("volumes", {}).get(key, 50)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(volume)
        slider.valueChanged.connect(lambda value, k=key: self._on_ambient_volume_changed(k, value))
        row.addWidget(slider)

        loop.set_volume(volume, apply=False)
        if not loop.available():
            check.setEnabled(False)
            check.setToolTip("File not found on disk" if removable
                              else "No audio player found (checked ffplay, paplay, pw-play, aplay)")
        elif not loop.likely_compatible():
            check.setToolTip(
                "This format may not play without ffplay installed — "
                "if it stays silent, try converting it to .wav"
            )

        if removable:
            remove_btn = QPushButton("✕")
            remove_btn.setFixedWidth(28)
            remove_btn.setToolTip("Remove from the default set (you can restore it later)" if is_builtin
                                   else "Remove this custom sound")
            if is_builtin:
                remove_btn.clicked.connect(lambda: self._hide_builtin_sound(key))
            else:
                remove_btn.clicked.connect(lambda: self._remove_custom_sound(custom_id))
            row.addWidget(remove_btn)

        self.ambient_rows_layout.addLayout(row)
        self.ambient_loops[key] = loop
        self.ambient_checks[key] = check
        self.ambient_sliders[key] = slider

    def _add_custom_sound(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an ambient sound file", "", AUDIO_FILE_FILTER)
        if not path:
            return
        default_label = os.path.splitext(os.path.basename(path))[0]
        label, ok = QInputDialog.getText(self, "Name this sound", "Label:", text=default_label)
        if not ok:
            return
        label = label.strip() or default_label
        self.storage.add_custom_sound(path, label)
        self._build_ambient_rows()

    def _remove_custom_sound(self, custom_id):
        self.storage.remove_custom_sound(custom_id)
        self._build_ambient_rows()

    def _hide_builtin_sound(self, key):
        self.storage.hide_builtin_sound(key)
        self._build_ambient_rows()

    def _restore_builtin_sounds(self):
        self.storage.restore_builtin_sounds()
        self._build_ambient_rows()

    def _format_time(self):
        m, s = divmod(self.seconds_left, 60)
        return f"{m:02d}:{s:02d}"

    def _phase_minutes(self, phase):
        s = self.storage.get_pomodoro_settings()
        return {
            "work": s["work_min"],
            "short_break": s["short_break_min"],
            "long_break": s["long_break_min"],
        }[phase]

    def _phase_ring_color(self):
        p = theme.current()
        # Work sessions get the full accent color; breaks use the softer
        # tint so the ring itself reads as "resting" at a glance.
        return p["ACCENT"] if self.phase == "work" else p["ACCENT_SOFT"]

    def toggle_running(self):
        if self.running:
            self._pause()
        else:
            self._start()

    def _start(self):
        self.running = True
        now = time.monotonic()
        self._deadline = now + self.seconds_left
        self._last_tick = now
        self.timer.start()
        self.start_btn.setText("⏸  Pause")
        self._sync_ambient_to_phase()

    def _pause(self):
        self._update_seconds_left()  # settle the exact time remaining
        self.running = False
        self.timer.stop()
        self.start_btn.setText("▶  Start")

    def _update_seconds_left(self):
        now = time.monotonic()
        gap = now - self._last_tick
        self._last_tick = now
        if gap > STALL_GAP_SECONDS:
            self._deadline += gap - TICK_MS / 1000  # forgive time spent asleep / frozen
        self.seconds_left = max(0, math.ceil(self._deadline - now))

    def reset_phase(self):
        self.timer.stop()
        self.running = False
        self.start_btn.setText("▶  Start")
        self.seconds_left = self._phase_minutes(self.phase) * 60
        self._refresh_labels()

    def advance_phase(self, completed=False):
        """Moves on to the next phase. `completed` is True when the timer
        ran out; only then does the phase earn credit (XP, a finished
        session, quest progress) and a notification. Skip passes False, so
        mashing Skip can't be used to farm XP."""
        self.timer.stop()
        self.running = False
        self.start_btn.setText("▶  Start")

        settings = self.storage.get_pomodoro_settings()
        if self.phase == "work":
            if completed:
                self.sessions_completed += 1
            long_break = completed and self.sessions_completed % settings["sessions_before_long_break"] == 0
            self.phase = "long_break" if long_break else "short_break"
            if completed:
                notify("Pomodoro", "Work session done — time for a long break." if long_break
                       else "Work session done — take a short break.")
                self._play_chime()
                result = self.storage.record_pomodoro_completed(settings["work_min"])
                congrats = quotes.random_pomodoro_congrats()
                self._announce_gamification(result, f"\U0001F43E {congrats}")
        else:
            self.phase = "work"
            if completed:
                notify("Pomodoro", "Break's over — back to work.")
                self._play_chime()
                break_result = self.storage.record_break_completed()
                self._announce_quests(break_result.get("completed_quests", []))

        self.seconds_left = self._phase_minutes(self.phase) * 60
        self._refresh_labels()
        if completed and settings.get("auto_start_next", True):
            self._start()
        else:
            self._sync_ambient_to_phase()

    def _play_chime(self):
        if self.storage.get_pomodoro_settings().get("chime", True):
            chime.play_chime()

    def _announce_gamification(self, result, headline):
        detail = f"+{result['xp_gained']} XP"
        if result["new_level"] > result["old_level"]:
            detail += f"  • Level up! Now level {result['new_level']}"
        self.celebrate.emit(headline, detail)
        self._announce_quests(result.get("completed_quests", []))
        # A little nerdy motivation to carry into the next session or break.
        quote, source = quotes.random_quote()
        self.celebrate.emit(f"“{quote}”", f"— {source}")

    def _announce_quests(self, completed_quests):
        for quest in completed_quests:
            self.celebrate.emit(
                "\U0001F31F Quest complete!",
                f"{quest['desc']}  • +{quest['bonus_xp']} XP",
            )

    def _on_tick(self):
        previous = self.seconds_left
        self._update_seconds_left()
        if self.seconds_left <= 0:
            self.advance_phase(completed=True)
        elif self.seconds_left != previous:
            self._refresh_labels()

    def _refresh_labels(self):
        phase_text = PHASE_NAMES[self.phase]
        total = self._phase_minutes(self.phase) * 60
        fraction = 1 - (self.seconds_left / total) if total else 0.0

        self.circular_timer.set_colors(
            self._phase_ring_color(), *self._track_and_ink_colors()
        )
        self.circular_timer.set_progress(fraction, self._format_time(), phase_text)
        self._update_session_dots()

        self.tick.emit(self._format_time(), phase_text, fraction, self.phase)

    def _track_and_ink_colors(self):
        p = theme.current()
        return p["PAPER_EDGE"], p["INK"], p["INK_SOFT"]

    def _update_session_dots(self):
        settings = self.storage.get_pomodoro_settings()
        total = settings["sessions_before_long_break"]
        completed_in_cycle = self.sessions_completed % total
        p = theme.current()
        dots = []
        for i in range(total):
            color = p["ACCENT"] if i < completed_in_cycle else p["PAPER_EDGE"]
            dots.append(f'<span style="color:{color};">●</span>')
        self.session_dots_label.setText(" ".join(dots))

    def refresh_theme(self):
        """Re-apply colors for the current palette. Call after a theme switch."""
        p = theme.current()
        self.ambient_status_label.setStyleSheet(f"color: {p['ACCENT']}; font-size: 10px;")
        self.circular_timer.set_colors(self._phase_ring_color(), *self._track_and_ink_colors())
        self._update_session_dots()

    def _toggle_ambient(self, key, checked):
        active = [k for k, c in self.ambient_checks.items() if c.isChecked()]
        if checked and len(active) > MAX_CONCURRENT:
            # over the limit — revert this one and let the user know
            self.ambient_checks[key].blockSignals(True)
            self.ambient_checks[key].setChecked(False)
            self.ambient_checks[key].blockSignals(False)
            self.ambient_status_label.setText(
                f"You can play up to {MAX_CONCURRENT} ambient sounds at once — turn one off first."
            )
            return
        self.ambient_status_label.setText("")

        loop = self.ambient_loops[key]
        if checked:
            loop.play()
        else:
            loop.stop()
        if not self._ambient_auto_changing:
            # remember what the user chose (the mix "work sessions only" replays)
            self.storage.set_ambient_mix([k for k, c in self.ambient_checks.items() if c.isChecked()])

    def _sync_ambient_to_phase(self):
        """"Ambient sounds only during work" mode: when a work session is
        running, bring up the remembered mix; during a break, silence it.
        Does nothing when that setting is off."""
        if not self.storage.get_pomodoro_settings().get("ambient_auto"):
            return
        self._ambient_auto_changing = True
        try:
            if self.phase == "work":
                if not self.running:
                    return
                mix = self.storage.get_ambient_prefs().get("mix", [])
                for key in mix[:MAX_CONCURRENT]:
                    check = self.ambient_checks.get(key)
                    if check is not None and check.isEnabled() and not check.isChecked():
                        check.setChecked(True)
            else:
                for check in self.ambient_checks.values():
                    if check.isChecked():
                        check.setChecked(False)
        finally:
            self._ambient_auto_changing = False

    def _on_ambient_volume_changed(self, key, value):
        # Apply immediately if nothing is playing (cheap: just remembers the
        # level for the next play()); otherwise debounce the actual restart
        # so dragging the slider doesn't thrash the audio subprocess.
        loop = self.ambient_loops[key]
        loop.set_volume(value, apply=False)
        self._pending_ambient_volume[key] = value

        timer = self._volume_debounce_timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._apply_pending_volume(k))
            self._volume_debounce_timers[key] = timer
        timer.start(VOLUME_APPLY_DELAY_MS)

    def _apply_pending_volume(self, key):
        loop = self.ambient_loops.get(key)
        value = self._pending_ambient_volume.get(key)
        if value is None:
            return
        self.storage.set_ambient_volume(key, value)  # remembered for next time
        if loop is not None and loop.is_playing():
            loop.set_volume(value, apply=True)

    def stop_all_ambient(self):
        for loop in self.ambient_loops.values():
            loop.stop()

    def save_settings(self):
        settings = {
            "work_min": self.work_spin.value(),
            "short_break_min": self.short_break_spin.value(),
            "long_break_min": self.long_break_spin.value(),
            "sessions_before_long_break": self.sessions_spin.value(),
            "chime": self.chime_check.isChecked(),
            "auto_start_next": self.auto_next_check.isChecked(),
            "ambient_auto": self.ambient_auto_check.isChecked(),
        }
        self.storage.set_pomodoro_settings(settings)
        if not self.running:
            self.seconds_left = self._phase_minutes(self.phase) * 60
        self._refresh_labels()
