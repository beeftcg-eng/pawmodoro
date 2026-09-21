"""
storage.py - Simple JSON-backed persistence for Pawmodoro.

Everything lives in ~/.local/share/pawmodoro/data.json
Autosaves happen constantly; nothing is ever lost unless you delete the file.
A dated copy is also kept in a `backups/` folder next to it (one per day, the
last two weeks), in case something ever does go wrong.

Cloud sync is local-first: every mutator changes local data and saves right
away, then queues the matching change in an outbox that sync_engine.py sends
in the background. The UI never waits on the network, and edits made while
offline are kept and uploaded later. See sync_engine.py.
"""
import json
import os
import shutil
import threading
import time
import uuid
from datetime import date, datetime, timedelta

import gamification
from paths import app_data_dir
from sync_engine import SyncEngine

APP_DIR = app_data_dir()
DATA_FILE = os.path.join(APP_DIR, "data.json")
BACKUP_DIR = os.path.join(APP_DIR, "backups")
BACKUP_KEEP = 14
BACKUP_REFRESH_SECONDS = 3600
HISTORY_KEEP_DAYS = 120

DEFAULT_DATA = {
    "notes": "",
    "checklist": [],  # list of {id, text, recurrence, last_completed, completed_today,
                       #          reminder_time ("HH:MM" or None), last_reminded (date or None),
                       #          awarded_on (date or None), weekday, source}
    "pomodoro": {
        "work_min": 25,
        "short_break_min": 5,
        "long_break_min": 15,
        "sessions_before_long_break": 4,
        "chime": True,             # play a sound when a phase ends
        "auto_start_next": True,   # roll straight into the next phase
        "ambient_auto": False,     # play the ambient mix only during work phases
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
    "ambient": {
        "volumes": {},  # sound key -> 0..100, remembered between runs
        "mix": [],      # sound keys last switched on (what "ambient_auto" replays)
    },
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
    "history": {},  # day isoformat -> {pomodoros, focus_min, tasks}
    "sync": {
        "enabled": False,
        "url": "",
        "anon_key": "",
        "email": "",
        "refresh_token": None,  # never the password itself, just a long-lived session token
    },
    "sync_outbox": [],  # changes waiting to be uploaded, oldest first (see sync_engine.py)
    "household": None,  # cached shared-list state: {id, invite_code, members, tasks} (see schema.sql)
}


def _deep_merge(defaults, loaded):
    """`defaults` overlaid with `loaded`, recursing into nested dicts, so a
    key added to a nested default in a newer version (e.g. a new pomodoro
    setting) shows up for existing users instead of raising a KeyError."""
    merged = json.loads(json.dumps(defaults))
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class Storage:
    """Loads data on start, keeps it in memory, and writes it back on every change."""

    def __init__(self):
        os.makedirs(APP_DIR, exist_ok=True)
        # Guards data.json writes and the outbox, which the sync thread also
        # touches. All other data is only ever touched from the UI thread.
        self._lock = threading.RLock()
        # Bumped by every local change the cloud should hear about, AND every
        # time a queued change finishes uploading. A pull that began before a
        # bump may predate that change, so it must not be applied. (Checking
        # only for "edits since" isn't enough: a pull fetched just before an
        # edit was uploaded would look fresh once the upload had finished.)
        self.rev = 0
        self._outbox_inflight = None
        self.data = self._load()
        self._outbox_seq = max((o.get("seq", 0) for o in self.data.get("sync_outbox", [])), default=0)
        self._backup_daily()
        self._rolled_date = date.today().isoformat()
        self._roll_recurring_tasks()
        # Created here, started by MainWindow once its UI callbacks are wired.
        self.sync = SyncEngine(self)

    # ---------- Loading / saving ----------

    def _load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return _deep_merge(DEFAULT_DATA, json.load(f))
            except (json.JSONDecodeError, OSError):
                # Corrupt file: back it up rather than destroying it silently
                backup = DATA_FILE + ".corrupt"
                try:
                    os.replace(DATA_FILE, backup)
                except OSError:
                    pass
                restored = self._load_newest_backup()
                if restored is not None:
                    print("[storage] data.json was unreadable; restored the newest daily backup")
                    return restored
        return json.loads(json.dumps(DEFAULT_DATA))

    def _load_newest_backup(self):
        if not os.path.isdir(BACKUP_DIR):
            return None
        for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
            try:
                with open(os.path.join(BACKUP_DIR, name), "r", encoding="utf-8") as f:
                    return _deep_merge(DEFAULT_DATA, json.load(f))
            except (json.JSONDecodeError, OSError):
                continue
        return None

    def _backup_daily(self):
        """Keeps one dated copy of data.json per day (the newest BACKUP_KEEP),
        refreshed at most once an hour so a restore loses little. The live
        file is only copied if it still parses, so a corrupt file can't
        overwrite a good backup."""
        if not os.path.exists(DATA_FILE):
            return
        target = os.path.join(BACKUP_DIR, f"data-{date.today().isoformat()}.json")
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            if not os.path.exists(target) or time.time() - os.path.getmtime(target) > BACKUP_REFRESH_SECONDS:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    json.load(f)
                shutil.copyfile(DATA_FILE, target)  # new mtime = when this snapshot was taken
                _restrict_permissions(target)
            for stale in sorted(os.listdir(BACKUP_DIR))[:-BACKUP_KEEP]:
                os.remove(os.path.join(BACKUP_DIR, stale))
        except (OSError, ValueError) as e:
            print(f"[storage] backup skipped: {e}")

    def save(self):
        with self._lock:
            # The sync thread also calls this; if another thread changes a
            # dict's size while it's being serialized, json raises
            # RuntimeError -- retry that instead of losing the write.
            for attempt in range(5):
                try:
                    text = json.dumps(self.data, indent=2)
                    break
                except RuntimeError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01)
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            _restrict_permissions(tmp)  # holds sync/Spotify tokens
            os.replace(tmp, DATA_FILE)

    # ---------- Sync outbox (see sync_engine.py) ----------

    def sync_configured(self):
        cfg = self.data.get("sync", {})
        return bool(cfg.get("enabled") and cfg.get("url") and cfg.get("anon_key") and cfg.get("refresh_token"))

    def _enqueue(self, op, args, key=None):
        """Queues a change for upload. `key` collapses repeats: a newer entry
        with the same key replaces an older one still waiting (e.g. only the
        latest notes text ever needs sending). Callers save() afterwards."""
        self._bump_rev()
        if not self.sync_configured():
            return
        with self._lock:
            outbox = self.data.setdefault("sync_outbox", [])
            if key is not None:
                # never drop the entry currently being sent
                outbox[:] = [o for o in outbox if o.get("key") != key or o["seq"] == self._outbox_inflight]
            self._outbox_seq += 1
            outbox.append({"seq": self._outbox_seq, "op": op, "args": args, "key": key})
        self.sync.wake()

    def _bump_rev(self):
        with self._lock:
            self.rev += 1

    def outbox_len(self):
        with self._lock:
            return len(self.data.get("sync_outbox", []))

    def outbox_peek(self):
        """The oldest waiting change (marked as being sent), or None."""
        with self._lock:
            outbox = self.data.get("sync_outbox", [])
            if not outbox:
                self._outbox_inflight = None
                return None
            self._outbox_inflight = outbox[0]["seq"]
            return json.loads(json.dumps(outbox[0]))

    def outbox_release(self):
        with self._lock:
            self._outbox_inflight = None

    def outbox_done(self, seq):
        with self._lock:
            outbox = self.data.get("sync_outbox", [])
            outbox[:] = [o for o in outbox if o["seq"] != seq]
            self._outbox_inflight = None
            self.rev += 1  # the server changed; any pull fetched before now is stale
            self.save()

    def persist_sync_token(self, token):
        """Supabase rotates the refresh token on every use, so the one on
        disk goes stale after any call that refreshed the session. Called
        by the sync thread after each network operation."""
        with self._lock:
            cfg = self.data.setdefault("sync", {})
            if token and cfg.get("refresh_token") != token:
                cfg["refresh_token"] = token
                self.save()

    # ---------- Notes ----------
    def get_notes(self):
        return self.data.get("notes", "")

    def set_notes(self, text):
        self.data["notes"] = text
        self._enqueue("set_notes", {"text": text}, key="notes")
        self.save()

    # ---------- Checklist ----------
    def _roll_recurring_tasks(self):
        """Un-checks tasks whose period has rolled over: daily tasks each new
        day; a "weekday" task (e.g. "take out the trash" pinned to your
        collection day) whenever that weekday comes around; a "weekly" task
        when the quest week rolls over (Tuesday). "once" tasks never reset."""
        today_date = date.today()
        today = today_date.isoformat()
        changed = False
        for task in self.data.get("checklist", []):
            recurrence = task.get("recurrence")
            last = task.get("last_completed")
            should_reset = (
                (recurrence == "daily" and last != today)
                or (recurrence == "weekday" and task.get("weekday") is not None
                    and today_date.weekday() == task["weekday"] and last != today)
                or (recurrence == "weekly" and gamification.weekly_task_needs_reset(last, today_date))
            )
            if should_reset and task.get("completed_today"):
                task["completed_today"] = False
                changed = True
        if changed:
            self.save()

    def roll_day_if_needed(self):
        """Called periodically by the UI: when the calendar day has changed
        since the last call (the app can sit in the tray for days), resets
        recurring tasks and quests. Returns True if a new day began. Also
        keeps the hourly data.json backup fresh."""
        self._backup_daily()  # cheap no-op unless it's been an hour
        today = date.today().isoformat()
        if today == self._rolled_date:
            return False
        self._rolled_date = today
        self._roll_recurring_tasks()
        self._ensure_daily_quests()
        self._ensure_weekly_quests()
        return True

    def _find_task(self, task_id):
        return next((t for t in self.data["checklist"] if t["id"] == task_id), None)

    def add_task(self, text, recurrence="daily", reminder_time=None, weekday=None, source="checklist"):
        task = {
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "recurrence": recurrence,  # "daily", "weekly", "once", "weekday"
            "last_completed": None,
            "completed_today": False,
            "reminder_time": reminder_time,  # "HH:MM" or None
            "weekday": weekday,  # 0=Monday..6=Sunday, only meaningful for recurrence="weekday"; local-only, doesn't sync
            "last_reminded": None,  # date isoformat, so a reminder fires at most once/day (local-only)
            "awarded_on": None,  # last date this task paid out XP, see gamification.task_already_awarded
            "source": source,  # "checklist" (default) or "wishlist" (pushed from Deckbuilder)
        }
        self.data["checklist"].append(task)
        self._enqueue_task_add(task)
        self.save()
        return task

    def _enqueue_task_add(self, task):
        # "weekday" tasks are local-only: the cloud's CHECK constraint on
        # checklist_tasks.recurrence doesn't allow that value (see
        # _apply_remote_state, which preserves them across a pull).
        if task["recurrence"] != "weekday":
            self._enqueue("add_task", {
                "id": task["id"], "text": task["text"], "recurrence": task["recurrence"],
                "reminder_time": task.get("reminder_time"), "source": task.get("source", "checklist"),
            })

    def rename_task(self, task_id, text):
        task = self._find_task(task_id)
        if task is None:
            return
        task["text"] = text
        if task["recurrence"] != "weekday":
            self._enqueue("rename_task", {"id": task_id, "text": text})
        self.save()

    def set_task_reminder(self, task_id, reminder_time):
        """reminder_time: "HH:MM" string, or None to clear the reminder."""
        task = self._find_task(task_id)
        if task is None:
            return
        task["reminder_time"] = reminder_time
        task["last_reminded"] = None
        if task["recurrence"] != "weekday":
            self._enqueue("set_task_reminder", {"id": task_id, "reminder_time": reminder_time})
        self.save()

    def check_due_reminders(self):
        """Returns the tasks whose reminder time has arrived (or passed, e.g.
        the computer was asleep at that minute) and haven't been reminded
        about yet today. Marks them reminded so this can be polled
        repeatedly without re-notifying."""
        now = datetime.now()
        today = now.date().isoformat()
        current_hm = now.strftime("%H:%M")
        due = []
        for task in self.data.get("checklist", []):
            reminder_time = task.get("reminder_time")
            if not reminder_time or task.get("completed_today"):
                continue
            if task.get("last_reminded") == today:
                continue
            if task.get("recurrence") == "weekday" and task.get("weekday") not in (None, now.weekday()):
                continue  # a specific-day task only nags on its own day
            if current_hm >= reminder_time:
                task["last_reminded"] = today
                due.append(task)
        if due:
            self.save()
        return due

    def remove_task(self, task_id):
        """Removes a task; returns (task, index) so the caller can offer
        an undo via restore_task(), or None if it didn't exist."""
        for index, task in enumerate(self.data["checklist"]):
            if task["id"] == task_id:
                del self.data["checklist"][index]
                if task["recurrence"] != "weekday":
                    self._enqueue("remove_task", {"id": task_id})
                self.save()
                return task, index
        return None

    def restore_task(self, task, index):
        """Puts back a task returned by remove_task(), under its old id."""
        checklist = self.data["checklist"]
        checklist.insert(min(index, len(checklist)), task)
        self._enqueue_task_add(task)
        self.save()

    def reorder_tasks(self, ordered_ids):
        """Reorders the checklist to match `ordered_ids` (a list of task ids
        in the desired order). Any id not in the current checklist is
        ignored; any current task not present in `ordered_ids` is kept,
        appended at the end, so a stale/partial list can't drop tasks."""
        by_id = {t["id"]: t for t in self.data["checklist"]}
        new_order = [by_id[i] for i in ordered_ids if i in by_id]
        remaining = [t for t in self.data["checklist"] if t["id"] not in ordered_ids]
        self.data["checklist"] = new_order + remaining
        # "weekday" tasks never exist in the cloud, so leave them out.
        remote_ids = [t["id"] for t in self.data["checklist"] if t["recurrence"] != "weekday"]
        self._enqueue("reorder_tasks", {"ids": remote_ids}, key="reorder")
        self.save()

    def set_task_done(self, task_id, done):
        """Ticks or un-ticks a task. Returns what was earned (for the UI to
        celebrate): {"xp_gained", "old_level", "new_level", "completed_quests"}
        the first time the task is completed in its period, else just
        {"completed_quests": []}. Un-ticking never takes XP back, and a task
        only pays out once per period, so ticking it repeatedly can't be
        farmed."""
        task = self._find_task(task_id)
        if task is None:
            return {"completed_quests": []}
        today = date.today()
        task["completed_today"] = done
        if done:
            task["last_completed"] = today.isoformat()
        result = {"completed_quests": []}
        if done and not gamification.task_already_awarded(task["recurrence"], task.get("awarded_on"), today):
            self._ensure_daily_quests()
            self._ensure_weekly_quests()
            result = self._award_task(task, today)
        if task["recurrence"] != "weekday":
            self._enqueue("complete_task", {"id": task_id, "done": done})
        self.save()
        return result

    def get_checklist(self):
        return self.data.get("checklist", [])

    # ---------- Pomodoro settings ----------
    def get_pomodoro_settings(self):
        return self.data.get("pomodoro", DEFAULT_DATA["pomodoro"])

    def set_pomodoro_settings(self, settings):
        self.data["pomodoro"] = dict(settings)
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

    # ---------- Ambient sounds ----------
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

    def get_ambient_prefs(self):
        return self.data.setdefault("ambient", {"volumes": {}, "mix": []})

    def set_ambient_volume(self, key, value):
        self.get_ambient_prefs().setdefault("volumes", {})[key] = int(value)
        self.save()

    def set_ambient_mix(self, keys):
        self.get_ambient_prefs()["mix"] = list(keys)
        self.save()

    # ---------- Spotify ----------
    def get_spotify_config(self):
        return self.data.get("spotify", dict(DEFAULT_DATA["spotify"]))

    def set_spotify_config(self, config):
        self.data["spotify"] = config
        self.save()

    # ---------- Cloud Sync (Supabase; see sync_settings_dialog.py) ----------
    def get_sync_config(self):
        return dict(self.data.get("sync", DEFAULT_DATA["sync"]))

    def save_sync_config(self, url, anon_key, email, refresh_token, enabled):
        old = self.data.get("sync", {})
        different_account = (old.get("url"), old.get("email")) != (url, email)
        self.data["sync"] = {
            "enabled": enabled, "url": url, "anon_key": anon_key,
            "email": email, "refresh_token": refresh_token,
        }
        if different_account or not enabled:
            # Queued changes and the cached household belong to the account
            # they were made under; never replay them into another one.
            with self._lock:
                self.data["sync_outbox"] = []
                self._outbox_inflight = None
            self.data["household"] = None
        self.save()
        self.sync.reconfigure()

    def _apply_remote_state(self, remote, keep_notes=False):
        """Overwrites the local cache with the server's copy of notes,
        checklist, and gamification state (server is authoritative once
        connected)."""
        if not keep_notes:
            self.data["notes"] = remote["notes"]
        local_tasks = {t["id"]: t for t in self.data.get("checklist", [])}
        # "weekday" (specific-day) tasks are local-only — the cloud
        # schema's checklist_tasks.recurrence CHECK constraint doesn't
        # allow that value, so they can never come back from the server.
        # Preserve them across this replace instead of silently deleting
        # them the next time any sync event pulls fresh server state.
        local_weekday_tasks = [t for t in local_tasks.values() if t.get("recurrence") == "weekday"]
        self.data["checklist"] = [
            {
                "id": t["id"],
                "text": t["text"],
                "recurrence": t["recurrence"],
                "last_completed": t.get("last_completed"),
                "completed_today": t.get("completed_today", False),
                "reminder_time": t.get("reminder_time"),
                # Reminders are tracked locally only (the server never
                # learns when we notified), so keep our own record instead
                # of letting every pull reset it and re-fire the reminder.
                "last_reminded": (local_tasks.get(t["id"]) or {}).get("last_reminded") or t.get("last_reminded"),
                "awarded_on": t.get("awarded_on"),
                "source": t.get("source", "checklist"),
            }
            for t in remote["checklist"]
        ] + local_weekday_tasks
        g = self.data["gamification"]
        g["xp"] = remote["xp"]
        g["total_pomodoros"] = remote["total_pomodoros"]
        g["total_tasks"] = remote["total_tasks"]
        g["current_streak"] = remote["current_streak"]
        g["longest_streak"] = remote["longest_streak"]
        g["quests"] = remote["quests"]
        g["weekly_quests"] = remote["weekly_quests"]
        today = date.today()
        g["quests_date"] = today.isoformat()
        g["weekly_quests_start"] = gamification.week_start_for(today).isoformat()
        self._merge_remote_history(remote.get("history") or [])

    def adopt_remote_state(self, remote, household=False, rev=None, keep_notes=False):
        """Replaces the local cache with the server's state (see
        sync_engine.py). `household`: False = leave the cached household
        alone, None = the account isn't in one, else the household dict.
        `keep_notes`: leave the notes text alone (the editor is being used).
        When `rev` is given (the value when the pull began), nothing is
        applied -- and False is returned -- if anything changed locally since
        or changes are still waiting to upload, so a stale pull can never
        overwrite a fresh edit. Returns True when applied."""
        if rev is not None and (rev != self.rev or self.outbox_len()):
            return False
        self._apply_remote_state(remote, keep_notes=keep_notes)
        if household is not False:
            self.data["household"] = household
        self.save()
        return True

    # ---------- History (focus minutes etc. per day) ----------
    def _history_bump(self, pomodoros=0, focus_min=0, tasks=0):
        history = self.data.setdefault("history", {})
        today = date.today().isoformat()
        row = history.setdefault(today, {"pomodoros": 0, "focus_min": 0, "tasks": 0})
        row["pomodoros"] += pomodoros
        row["focus_min"] += focus_min
        row["tasks"] += tasks
        cutoff = (date.today() - timedelta(days=HISTORY_KEEP_DAYS)).isoformat()
        for day in [d for d in history if d < cutoff]:
            del history[day]

    def _merge_remote_history(self, rows):
        history = self.data.setdefault("history", {})
        for row in rows:
            mine = history.get(row["day"], {})
            history[row["day"]] = {
                key: max(mine.get(key, 0), row.get(key, 0)) for key in ("pomodoros", "focus_min", "tasks")
            }

    def get_history(self, days=14):
        """The last `days` days, oldest first, zero-filled:
        [{"day": date, "pomodoros", "focus_min", "tasks"}, ...]."""
        history = self.data.get("history", {})
        today = date.today()
        out = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            row = history.get(day.isoformat(), {})
            out.append({"day": day, "pomodoros": row.get("pomodoros", 0),
                        "focus_min": row.get("focus_min", 0), "tasks": row.get("tasks", 0)})
        return out

    def get_week_totals(self):
        """Totals since the quest week started (Tuesday), the same window the
        household "this week" numbers use."""
        start = gamification.week_start_for(date.today()).isoformat()
        totals = {"pomodoros": 0, "focus_min": 0, "tasks": 0}
        for day, row in self.data.get("history", {}).items():
            if day >= start:
                for key in totals:
                    totals[key] += row.get(key, 0)
        return totals

    # ---------- Household (shared list; see schema.sql) ----------
    def get_household(self):
        return self.data.get("household")

    def set_household(self, household):
        """Stores the household returned by the create/join calls (or None
        after leaving)."""
        self.data["household"] = household
        self._bump_rev()
        self.save()

    def household_my_name(self):
        household = self.get_household() or {}
        for member in household.get("members", []):
            if member.get("is_me"):
                return member.get("name") or "Me"
        return "Me"

    def shared_add_task(self, text):
        household = self.get_household()
        if not household:
            return None
        task = {"id": uuid.uuid4().hex[:12], "text": text, "done": False, "done_by_name": None, "done_at": None}
        household["tasks"].append(task)
        self._enqueue("shared_add", {"id": task["id"], "text": text})
        self.save()
        return task

    def shared_set_done(self, task_id, done):
        household = self.get_household()
        for task in (household or {}).get("tasks", []):
            if task["id"] == task_id:
                task["done"] = done
                task["done_by_name"] = self.household_my_name() if done else None
                task["done_at"] = datetime.now().isoformat() if done else None
        self._enqueue("shared_done", {"id": task_id, "done": done})
        self.save()

    def shared_remove_task(self, task_id):
        household = self.get_household()
        if household:
            household["tasks"] = [t for t in household["tasks"] if t["id"] != task_id]
        self._enqueue("shared_remove", {"id": task_id})
        self.save()

    def shared_clear_done(self):
        household = self.get_household()
        if household:
            household["tasks"] = [t for t in household["tasks"] if not t["done"]]
        self._enqueue("shared_clear_done", {}, key="shared_clear_done")
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

    def record_pomodoro_completed(self, minutes=None):
        """Call once per completed work session (not for a skipped one).
        `minutes` is the session length, for the focus-time history.
        Returns a dict describing what was earned, for the UI to celebrate."""
        if minutes is None:
            minutes = self.get_pomodoro_settings().get("work_min", 25)
        self._ensure_daily_quests()
        self._ensure_weekly_quests()
        self._bump_streak()
        g = self.data["gamification"]
        g["total_pomodoros"] += 1
        self._history_bump(pomodoros=1, focus_min=minutes)
        old_level, _, _ = gamification.level_from_xp(g["xp"])
        self.add_xp(gamification.XP_PER_WORK_SESSION)
        completed_quests = self._advance_quests("pomodoros", 1)
        new_level, _, _ = gamification.level_from_xp(g["xp"])
        self._enqueue("pomodoro", {"minutes": minutes})
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
        self._enqueue("break", {})
        self.save()
        return {"completed_quests": completed_quests}

    def _award_task(self, task, today):
        """The XP/streak/quest payout for a task's first completion in its
        period. Mirrors award_task() in supabase/schema.sql."""
        g = self.data["gamification"]
        xp = gamification.XP_PER_TASK.get(task["recurrence"], 10)
        self._bump_streak()
        g["total_tasks"] += 1
        self._history_bump(tasks=1)
        old_level, _, _ = gamification.level_from_xp(g["xp"])
        self.add_xp(xp)
        completed_quests = self._advance_quests("tasks", 1)
        real_tasks = [t for t in self.data["checklist"] if t.get("source", "checklist") == "checklist"]
        if real_tasks and all(t.get("completed_today") for t in real_tasks):
            completed_quests += self._advance_quests("clear_checklist", 1)
        task["awarded_on"] = today.isoformat()
        new_level, _, _ = gamification.level_from_xp(g["xp"])
        return {"xp_gained": xp, "old_level": old_level, "new_level": new_level,
                "completed_quests": completed_quests}


def _restrict_permissions(path):
    """data.json holds login tokens, so keep it (and its backups) private to
    this user. No-op where chmod doesn't apply (Windows)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
