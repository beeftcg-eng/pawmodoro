"""
storage.py - Simple JSON-backed persistence for Pawmodoro.

Everything lives in ~/.local/share/pawmodoro/data.json
Autosaves happen constantly; nothing is ever lost unless you delete the file.
"""
import json
import os
import uuid
from datetime import date, datetime, timedelta

import gamification
from paths import app_data_dir

APP_DIR = app_data_dir()
DATA_FILE = os.path.join(APP_DIR, "data.json")

DEFAULT_DATA = {
    "notes": "",
    "checklist": [],  # list of {id, text, recurrence, last_completed, completed_today,
                       #          reminder_time ("HH:MM" or None), last_reminded (date or None)}
    "pomodoro": {
        "work_min": 25,
        "short_break_min": 5,
        "long_break_min": 15,
        "sessions_before_long_break": 4,
    },
    "window": {
        "widget_mode": False,
        "widget_x": 100,
        "widget_y": 100,
        "widget_w": 260,
        "widget_h": 360,
    },
    "theme": "Paper",
    "custom_sounds": [],  # list of {id, label, path}
    "hidden_builtin_sounds": [],  # list of AMBIENT_SOUNDS keys the user removed
    "spotify": {
        "client_id": "",
        "access_token": None,
        "refresh_token": None,
        "expires_at": 0,
    },
    "gamification": {
        "xp": 0,
        "total_pomodoros": 0,
        "total_tasks": 0,
        "current_streak": 0,
        "longest_streak": 0,
        "last_active_date": None,  # date isoformat of the last day XP was earned
        "quests_date": None,  # date isoformat the current quest list was generated for
        "quests": [],  # list of {id, kind, target, desc, progress, completed}
        "weekly_quests_start": None,  # isoformat of the Tuesday the current weekly quests started
        "weekly_quests": [],  # list of {id, kind, target, desc, progress, completed}
    },
}


class Storage:
    """Loads data on start, keeps it in memory, and writes it back on every change."""

    def __init__(self):
        os.makedirs(APP_DIR, exist_ok=True)
        self.data = self._load()
        self._roll_recurring_tasks()

    def _load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Merge with defaults so upgrades never crash on missing keys
                merged = json.loads(json.dumps(DEFAULT_DATA))
                merged.update(loaded)
                for key in DEFAULT_DATA:
                    if key not in merged:
                        merged[key] = DEFAULT_DATA[key]
                return merged
            except (json.JSONDecodeError, OSError):
                # Corrupt file: back it up rather than destroying it silently
                backup = DATA_FILE + ".corrupt"
                try:
                    os.replace(DATA_FILE, backup)
                except OSError:
                    pass
        return json.loads(json.dumps(DEFAULT_DATA))

    def save(self):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, DATA_FILE)

    # ---------- Notes ----------
    def get_notes(self):
        return self.data.get("notes", "")

    def set_notes(self, text):
        self.data["notes"] = text
        self.save()

    # ---------- Checklist ----------
    def _roll_recurring_tasks(self):
        """Reset 'completed_today' for daily tasks whose last_completed isn't today."""
        today = date.today().isoformat()
        changed = False
        for task in self.data.get("checklist", []):
            if task.get("recurrence") == "daily" and task.get("last_completed") != today:
                if task.get("completed_today"):
                    changed = True
                task["completed_today"] = False
        if changed:
            self.save()

    def add_task(self, text, recurrence="daily", reminder_time=None):
        task = {
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "recurrence": recurrence,  # "daily", "weekly", "once"
            "last_completed": None,
            "completed_today": False,
            "reminder_time": reminder_time,  # "HH:MM" or None
            "last_reminded": None,  # date isoformat, so a reminder fires at most once/day
        }
        self.data["checklist"].append(task)
        self.save()
        return task

    def set_task_reminder(self, task_id, reminder_time):
        """reminder_time: "HH:MM" string, or None to clear the reminder."""
        for task in self.data["checklist"]:
            if task["id"] == task_id:
                task["reminder_time"] = reminder_time
                task["last_reminded"] = None
        self.save()

    def check_due_reminders(self):
        """Returns the list of tasks whose reminder time matches right now
        (to the minute) and haven't already been reminded about today.
        Marks them as reminded so calling this repeatedly (it's driven by a
        polling timer) doesn't re-notify every tick within that minute."""
        now = datetime.now()
        today = now.date().isoformat()
        current_hm = now.strftime("%H:%M")
        due = []
        changed = False
        for task in self.data.get("checklist", []):
            reminder_time = task.get("reminder_time")
            if not reminder_time or task.get("completed_today"):
                continue
            if task.get("last_reminded") == today:
                continue
            if reminder_time == current_hm:
                task["last_reminded"] = today
                due.append(task)
                changed = True
        if changed:
            self.save()
        return due

    def remove_task(self, task_id):
        self.data["checklist"] = [t for t in self.data["checklist"] if t["id"] != task_id]
        self.save()

    def set_task_done(self, task_id, done):
        today = date.today().isoformat()
        for task in self.data["checklist"]:
            if task["id"] == task_id:
                task["completed_today"] = done
                task["last_completed"] = today if done else task.get("last_completed")
                if task["recurrence"] == "once" and done:
                    pass  # left in list, shown as done; user can delete manually
        self.save()

    def get_checklist(self):
        return self.data.get("checklist", [])

    # ---------- Pomodoro settings ----------
    def get_pomodoro_settings(self):
        return self.data.get("pomodoro", DEFAULT_DATA["pomodoro"])

    def set_pomodoro_settings(self, settings):
        self.data["pomodoro"] = settings
        self.save()

    # ---------- Window state ----------
    def get_window_state(self):
        return self.data.get("window", DEFAULT_DATA["window"])

    def set_window_state(self, state):
        self.data["window"] = state
        self.save()

    # ---------- Theme ----------
    def get_theme(self):
        return self.data.get("theme", "Paper")

    def set_theme(self, name):
        self.data["theme"] = name
        self.save()

    # ---------- Custom ambient sounds ----------
    def get_custom_sounds(self):
        return self.data.get("custom_sounds", [])

    def add_custom_sound(self, path, label):
        sound = {"id": uuid.uuid4().hex[:8], "label": label, "path": path}
        self.data.setdefault("custom_sounds", []).append(sound)
        self.save()
        return sound

    def remove_custom_sound(self, sound_id):
        self.data["custom_sounds"] = [
            s for s in self.data.get("custom_sounds", []) if s["id"] != sound_id
        ]
        self.save()

    def get_hidden_builtin_sounds(self):
        return self.data.get("hidden_builtin_sounds", [])

    def hide_builtin_sound(self, key):
        hidden = self.data.setdefault("hidden_builtin_sounds", [])
        if key not in hidden:
            hidden.append(key)
        self.save()

    def restore_builtin_sounds(self):
        self.data["hidden_builtin_sounds"] = []
        self.save()

    # ---------- Spotify ----------
    def get_spotify_config(self):
        return self.data.get("spotify", dict(DEFAULT_DATA["spotify"]))

    def set_spotify_config(self, config):
        self.data["spotify"] = config
        self.save()

    # ---------- Gamification: XP, levels, streaks, daily/weekly quests ----------
    def _ensure_daily_quests(self):
        """Generates a fresh quest list the first time it's touched on a
        new day. Cheap enough to call at the top of every gamification
        method rather than tracking "has today rolled over yet" separately."""
        g = self.data.setdefault("gamification", json.loads(json.dumps(DEFAULT_DATA["gamification"])))
        today = date.today().isoformat()
        if g.get("quests_date") != today:
            g["quests_date"] = today
            g["quests"] = gamification.generate_daily_quests(today)
            self.save()

    def _ensure_weekly_quests(self):
        """Generates a fresh weekly quest list the first time it's touched
        since the last Tuesday reset."""
        g = self.data.setdefault("gamification", json.loads(json.dumps(DEFAULT_DATA["gamification"])))
        week_start = gamification.week_start_for(date.today()).isoformat()
        if g.get("weekly_quests_start") != week_start:
            g["weekly_quests_start"] = week_start
            g["weekly_quests"] = gamification.generate_weekly_quests(week_start)
            self.save()

    def get_gamification(self):
        self._ensure_daily_quests()
        self._ensure_weekly_quests()
        return self.data["gamification"]

    def _bump_streak(self):
        g = self.data["gamification"]
        today = date.today().isoformat()
        last = g.get("last_active_date")
        if last == today:
            return
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        g["current_streak"] = g["current_streak"] + 1 if last == yesterday else 1
        g["last_active_date"] = today
        g["longest_streak"] = max(g.get("longest_streak", 0), g["current_streak"])

    def add_xp(self, amount):
        """Adds (or, with a negative amount, removes) XP, clamped at zero."""
        self._ensure_daily_quests()
        self._ensure_weekly_quests()
        g = self.data["gamification"]
        g["xp"] = max(0, g["xp"] + amount)
        self.save()

    def _advance_quest_list(self, quests, kind, amount, quest_bonus_xp, all_bonus_xp):
        """Progresses every incomplete quest of `kind` in `quests` by
        `amount`, awards bonus XP for any that just completed (plus an
        extra bonus if that completed the whole list), and returns the
        list of quests newly completed by this call."""
        newly_completed = []
        for quest in quests:
            if quest["kind"] != kind or quest["completed"]:
                continue
            quest["progress"] = min(quest["target"], quest["progress"] + amount)
            if quest["progress"] >= quest["target"]:
                quest["completed"] = True
                quest["bonus_xp"] = quest_bonus_xp
                newly_completed.append(dict(quest))
        if newly_completed:
            self.add_xp(len(newly_completed) * quest_bonus_xp)
            if quests and all(q["completed"] for q in quests):
                self.add_xp(all_bonus_xp)
        return newly_completed

    def _advance_quests(self, kind, amount):
        """Progresses every incomplete daily and weekly quest of `kind`,
        and returns the combined list of quests newly completed by this
        call."""
        g = self.data["gamification"]
        completed = self._advance_quest_list(
            g.get("quests", []), kind, amount,
            gamification.QUEST_BONUS_XP, gamification.ALL_QUESTS_BONUS_XP,
        )
        completed += self._advance_quest_list(
            g.get("weekly_quests", []), kind, amount,
            gamification.WEEKLY_QUEST_BONUS_XP, gamification.ALL_WEEKLY_QUESTS_BONUS_XP,
        )
        return completed

    def record_pomodoro_completed(self):
        """Call once per completed work session. Returns a dict describing
        what was earned, for the UI to celebrate."""
        self._ensure_daily_quests()
        self._ensure_weekly_quests()
        self._bump_streak()
        g = self.data["gamification"]
        g["total_pomodoros"] += 1
        old_level, _, _ = gamification.level_from_xp(g["xp"])
        self.add_xp(gamification.XP_PER_WORK_SESSION)
        completed_quests = self._advance_quests("pomodoros", 1)
        new_level, _, _ = gamification.level_from_xp(g["xp"])
        self.save()
        return {
            "xp_gained": gamification.XP_PER_WORK_SESSION,
            "old_level": old_level,
            "new_level": new_level,
            "completed_quests": completed_quests,
        }

    def record_break_completed(self):
        """Call once per completed break. Breaks don't earn XP on their
        own, but they can satisfy a "take a break" quest."""
        self._ensure_daily_quests()
        self._ensure_weekly_quests()
        completed_quests = self._advance_quests("breaks", 1)
        self.save()
        return {"completed_quests": completed_quests}

    def record_task_event(self, recurrence, done):
        """Call whenever a checklist checkbox is toggled. `done=True`
        awards XP and progresses quests; `done=False` (unchecking) reverses
        the same XP so toggling back and forth nets to zero."""
        self._ensure_daily_quests()
        self._ensure_weekly_quests()
        xp = gamification.XP_PER_TASK.get(recurrence, 10)
        g = self.data["gamification"]
        result = {"completed_quests": []}
        if done:
            self._bump_streak()
            g["total_tasks"] += 1
            old_level, _, _ = gamification.level_from_xp(g["xp"])
            self.add_xp(xp)
            completed_quests = self._advance_quests("tasks", 1)
            if self.data["checklist"] and all(t.get("completed_today") for t in self.data["checklist"]):
                completed_quests += self._advance_quests("clear_checklist", 1)
            new_level, _, _ = gamification.level_from_xp(g["xp"])
            result.update(xp_gained=xp, old_level=old_level, new_level=new_level,
                           completed_quests=completed_quests)
        else:
            g["total_tasks"] = max(0, g["total_tasks"] - 1)
            self.add_xp(-xp)
        self.save()
        return result
