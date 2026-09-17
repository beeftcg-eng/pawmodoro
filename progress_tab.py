"""
progress_tab.py - Level, XP, streak, and today's/this week's quests: the
"gamification" dashboard tying pomodoro sessions and checklist tasks
together into one sense of daily and weekly progress.
"""
from datetime import date

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QProgressBar, QScrollArea
)
from PyQt6.QtCore import Qt

import theme
import gamification
import quotes

QUEST_ICONS = {
    "pomodoros": "\U0001F43E",  # paw
    "tasks": "✅",
    "breaks": "☕",
    "clear_checklist": "\U0001F9F9",
}


class ProgressTab(QWidget):
    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(14)

        # --- Level card ---
        self.level_box = QGroupBox("Your progress")
        level_layout = QVBoxLayout(self.level_box)

        self.level_label = QLabel("")
        self.level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        level_layout.addWidget(self.level_label)

        self.xp_bar = QProgressBar()
        self.xp_bar.setTextVisible(True)
        level_layout.addWidget(self.xp_bar)

        self.streak_label = QLabel("")
        self.streak_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        level_layout.addWidget(self.streak_label)

        stats_row = QHBoxLayout()
        self.pomodoro_stat_label = QLabel("")
        self.task_stat_label = QLabel("")
        stats_row.addWidget(self.pomodoro_stat_label)
        stats_row.addStretch()
        stats_row.addWidget(self.task_stat_label)
        level_layout.addLayout(stats_row)

        layout.addWidget(self.level_box)

        # --- Quote ---
        self.quote_box = QGroupBox("Quote")
        quote_layout = QVBoxLayout(self.quote_box)

        self.quote_label = QLabel("")
        self.quote_label.setWordWrap(True)
        self.quote_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        quote_layout.addWidget(self.quote_label)

        self.quote_source_label = QLabel("")
        self.quote_source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        quote_layout.addWidget(self.quote_source_label)

        layout.addWidget(self.quote_box)

        # --- Daily quests ---
        self.quests_box = QGroupBox("Today's quests")
        self.quests_layout = QVBoxLayout(self.quests_box)
        layout.addWidget(self.quests_box)

        # --- Weekly quests ---
        self.weekly_quests_box = QGroupBox("This week's quests")
        weekly_box_layout = QVBoxLayout(self.weekly_quests_box)

        self.weekly_reset_label = QLabel("")
        self.weekly_reset_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        weekly_box_layout.addWidget(self.weekly_reset_label)

        self.weekly_quests_layout = QVBoxLayout()
        weekly_box_layout.addLayout(self.weekly_quests_layout)

        layout.addWidget(self.weekly_quests_box)

        layout.addStretch()

        self.refresh_theme()

    def refresh(self, new_quote=False):
        g = self.storage.get_gamification()
        level, xp_into, xp_needed = gamification.level_from_xp(g["xp"])
        title = gamification.title_for_level(level)
        self.level_label.setText(f"\U0001F3C6 Level {level} — {title}")
        self.xp_bar.setRange(0, xp_needed)
        self.xp_bar.setValue(xp_into)
        self.xp_bar.setFormat(f"{xp_into} / {xp_needed} XP")

        streak = g.get("current_streak", 0)
        if streak >= 2:
            self.streak_label.setText(f"\U0001F525 {streak}-day streak (best: {g.get('longest_streak', streak)})")
        else:
            self.streak_label.setText("Complete something today to start a streak")

        self.pomodoro_stat_label.setText(f"\U0001F345 {g.get('total_pomodoros', 0)} sessions completed")
        self.task_stat_label.setText(f"✅ {g.get('total_tasks', 0)} tasks completed")

        # Only actually rolls a new quote when asked to (tab switch) or on
        # first load — refresh() itself also runs on every 5s cloud-sync
        # poll and other data updates, which would otherwise flip the quote
        # far faster than "each time you switch to the tab".
        if new_quote or not self.quote_label.text():
            quote, source = quotes.random_quote()
            self.quote_label.setText(f"“{quote}”")
            self.quote_source_label.setText(f"— {source}")

        self._rebuild_quests(self.quests_layout, g.get("quests", []), "No quests today.")

        days_left = gamification.days_until_weekly_reset(date.today())
        if days_left <= 1:
            self.weekly_reset_label.setText("⏳ Resets tomorrow (Tuesday)")
        else:
            self.weekly_reset_label.setText(f"⏳ Resets in {days_left} days (Tuesday)")
        self._rebuild_quests(self.weekly_quests_layout, g.get("weekly_quests", []), "No quests this week.")

    def _rebuild_quests(self, target_layout, quests, empty_text):
        while target_layout.count():
            item = target_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        if not quests:
            target_layout.addWidget(QLabel(empty_text))
            return

        p = theme.current()
        for quest in quests:
            icon = QUEST_ICONS.get(quest["kind"], "•")
            box = "☑" if quest["completed"] else "☐"
            text = f"{box} {icon} {quest['desc']}  ({quest['progress']}/{quest['target']})"
            label = QLabel(text)
            if quest["completed"]:
                label.setStyleSheet(f"color: {p['ACCENT']}; font-weight: bold;")
            target_layout.addWidget(label)

    def refresh_theme(self):
        p = theme.current()
        self.level_label.setFont(theme.serif_font(15, bold=True))
        self.streak_label.setStyleSheet(f"color: {p['ACCENT']};")

        quote_font = theme.serif_font(12)
        quote_font.setItalic(True)
        self.quote_label.setFont(quote_font)
        self.quote_label.setStyleSheet(f"color: {p['INK']};")
        self.quote_source_label.setStyleSheet(f"color: {p['INK_SOFT']}; font-size: 10px;")
        self.weekly_reset_label.setStyleSheet(f"color: {p['INK_SOFT']}; font-size: 10px;")

        self.refresh()
