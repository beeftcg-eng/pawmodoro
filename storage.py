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
import random
import os
import shutil
import threading
import time
import uuid
from datetime import date, datetime, timedelta

import gamification
import shared_schedule
import task_dates
from paths import app_data_dir
from sync_engine import SyncEngine

APP_DIR = app_data_dir()
DATA_FILE = os.path.join(APP_DIR, "data.json")
BACKUP_DIR = os.path.join(APP_DIR, "backups")
BACKUP_KEEP = 14
BACKUP_REFRESH_SECONDS = 3600
HISTORY_KEEP_DAYS = 120

DEFAULT_DATA = {
    "notes": "",  # the main notes page (the one that syncs)
    "notes_rev": None,  # the cloud revision of `notes` this computer last saw (conflict check)
    "notes_conflicts": [],  # other devices' versions to keep as pages: {page_id, title, html, at}
    "notes_pages": [],  # extra notes pages: list of {id, title, html} (sync from v2.16, table notes_pages)
    "notes_pages_synced": False,  # every local page has been queued for upload to this account
    "notes_current_page": "main",  # "main" or a notes_pages id
    "checklist": [],  # list of {id, text, recurrence, last_completed, completed_today,
                       #          reminder_time ("HH:MM" or None), last_reminded (date or None),
                       #          awarded_on (date or None), weekday, source,
                       #          reminder_every_h (int or None), last_reminded_at (datetime or None),
                       #          snoozed_until (datetime or None), focus_pomodoros, focus_min,
                       #          month_day (monthly), due_date (once), note, subtasks: [{id, text, done}]}
                       # (reminder_every_h and snoozed_until sync from v2.17, monthly / due dates /
                       # notes / subtasks from v2.18; last_reminded_at and the 🍅 counts stay local)
    "pomodoro": {
        "work_min": 25,
        "short_break_min": 5,
        "long_break_min": 15,
        "sessions_before_long_break": 4,
        "chime": True,             # play a sound when a phase ends
        "auto_start_next": True,   # roll straight into the next phase
        "ambient_auto": False,     # play the ambient mix only during work phases
        "idle_pause_min": 5,       # pause a work session after this long with no keyboard/mouse (0 = never)
    },
    "timer_state": None,  # the pomodoro timer across restarts, see PomodoroTab._save_timer_state
    "focus_task": None,  # checklist task id the pomodoro timer is "working on", or None
    "focus_auto_pick": True,  # pick a random one-time task for the timer when it has none (local-only)
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
    "focus_log": {},  # day isoformat -> {task id: {text, pomodoros, focus_min}} (local-only, pruned like history)
    "sync": {
        "enabled": False,
        "url": "",
        "anon_key": "",
        "email": "",
        "refresh_token": None,  # never the password itself, just a long-lived session token
    },
    "sync_outbox": [],  # changes waiting to be uploaded, oldest first (see sync_engine.py)
    "household": None,  # cached shared-list state: {id, invite_code, members, tasks} (see schema.sql)
    "updates": {
        "auto_check": True,  # look for a newer release at startup (see update_checker.py); never installs by itself
        "notified_version": None,  # the newest version we've already sent a notification about
    },
    "shared_notify": True,  # notify when another household member adds or completes a shared item (see shared_activity.py)
    "reminders": {
        # Repeating ("every N hours") reminders hold off between these times
        # and catch up once, when the quiet window ends.
        "quiet_enabled": False,
        "quiet_start": "22:00",
        "quiet_end": "08:00",
    },
    "shared_reminded": {},  # shared task id -> occurrence date its reminder already fired for (local-only)
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
        self._roll_shared_tasks()
        # Created here, started by MainWindow once its UI callbacks are wired.
        self.sync = SyncEngine(self)
        self._queue_unsynced_pages()

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

    # ---------- Backups you can pick from (View → Restore from a backup…) ----------
    def list_backups(self):
        """[(path, modified datetime)], newest first: the daily backups plus
        the copy kept from just before the last restore."""
        found = []
        candidates = [os.path.join(BACKUP_DIR, n) for n in (os.listdir(BACKUP_DIR) if os.path.isdir(BACKUP_DIR) else [])]
        candidates.append(os.path.join(APP_DIR, "data-before-restore.json"))
        for path in candidates:
            if path.endswith(".json") and os.path.isfile(path):
                found.append((path, datetime.fromtimestamp(os.path.getmtime(path))))
        return sorted(found, key=lambda item: item[1], reverse=True)

    def restore_from_file(self, path):
        """Replaces all local data with a backup / export file. Raises
        ValueError if it isn't Pawmodoro data. Keeps what a backup shouldn't
        undo: the cloud and Spotify logins, and (empties) the upload queue,
        whose entries belonged to the replaced data. The current data is
        kept as data-before-restore.json first, so a restore can be undone.
        The app should restart afterwards (every tab caches what it shows)."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"couldn't read that file ({e})") from e
        if not isinstance(loaded, dict) or not isinstance(loaded.get("checklist", []), list) or "notes" not in loaded:
            raise ValueError("that isn't a Pawmodoro data file")
        restored = _deep_merge(DEFAULT_DATA, loaded)
        with self._lock:
            before = os.path.join(APP_DIR, "data-before-restore.json")
            if os.path.exists(DATA_FILE) and os.path.abspath(path) != os.path.abspath(before):
                shutil.copyfile(DATA_FILE, before)
                _restrict_permissions(before)
            restored["sync"] = self.data.get("sync", DEFAULT_DATA["sync"])
            restored["spotify"] = self.data.get("spotify", DEFAULT_DATA["spotify"])
            restored["sync_outbox"] = []
            self._outbox_inflight = None
            restored["notes_pages_synced"] = False  # re-upload the restored pages
            self.data = restored
            self.rev += 1
        self.save()

    def export_data(self):
        """A copy of all data that's safe to hand around: no login tokens."""
        data = json.loads(json.dumps(self.data))
        data["sync"]["refresh_token"] = None
        data["spotify"].update(access_token=None, refresh_token=None)
        data["sync_outbox"] = []
        return data

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

    # Notes pages: "main" is data["notes"] (synced, what the phone edits);
    # any others live only in this file.
    MAIN_NOTES_PAGE = "main"

    def get_note_pages(self):
        """[{"id", "title"}], the main page first."""
        return [{"id": self.MAIN_NOTES_PAGE, "title": "Notes"}] + [
            {"id": p["id"], "title": p["title"]} for p in self.data.get("notes_pages", [])]

    def _find_note_page(self, page_id):
        return next((p for p in self.data.get("notes_pages", []) if p["id"] == page_id), None)

    def get_page_html(self, page_id):
        if page_id == self.MAIN_NOTES_PAGE:
            return self.get_notes()
        page = self._find_note_page(page_id)
        return page["html"] if page else ""

    def set_page_html(self, page_id, html):
        if page_id == self.MAIN_NOTES_PAGE:
            self.set_notes(html)
            return
        page = self._find_note_page(page_id)
        if page is not None and page["html"] != html:
            page["html"] = html
            self._enqueue_page(page)
            self.save()

    def _enqueue_page(self, page):
        # Keyed per page: only its latest title + text ever needs sending.
        self._enqueue("note_page_set", {"id": page["id"], "title": page["title"], "html": page["html"]},
                      key=f"page:{page['id']}")

    def _enqueue_page_order(self):
        self._enqueue("note_pages_reorder", {"ids": [p["id"] for p in self.data.get("notes_pages", [])]},
                      key="page_order")

    def _queue_unsynced_pages(self):
        """Uploads every local page once per account: pages made before
        pages synced (v2.15), or while the cloud schema was older. A pull
        never replaces local pages until this has happened."""
        if self.data.get("notes_pages_synced") or not self.sync_configured():
            return
        for page in self.data.get("notes_pages", []):
            self._enqueue_page(page)
        if self.data.get("notes_pages"):
            self._enqueue_page_order()
        self.data["notes_pages_synced"] = True
        self.save()

    # ---- conflict-checked notes saves (called by the sync thread) ----
    def outbox_base_rev(self, page_id):
        """The cloud revision this computer's copy of a page is based on."""
        with self._lock:
            if page_id == self.MAIN_NOTES_PAGE:
                return self.data.get("notes_rev")
            page = self._find_note_page(page_id)
            return page.get("rev") if page else None

    def outbox_saved_rev(self, page_id, rev):
        with self._lock:
            if page_id == self.MAIN_NOTES_PAGE:
                self.data["notes_rev"] = rev
            else:
                page = self._find_note_page(page_id)
                if page is not None:
                    page["rev"] = rev
            self.save()

    def outbox_notes_conflict(self, page_id, title, html, rev):
        """Someone else saved this page since we last saw it. Their version
        is kept (the UI thread turns it into a page, take_notes_conflicts),
        and ours then goes up on top of the revision we were just told."""
        with self._lock:
            self.data.setdefault("notes_conflicts", []).append({
                "page_id": page_id, "title": title, "html": html,
                "at": datetime.now().isoformat(timespec="minutes"),
            })
            if page_id == self.MAIN_NOTES_PAGE:
                self.data["notes_rev"] = rev
            else:
                page = self._find_note_page(page_id)
                if page is not None:
                    page["rev"] = rev
            self.save()

    def take_notes_conflicts(self):
        """UI thread: keeps each other-device version as a new page.
        Returns the titles of the pages made."""
        with self._lock:
            conflicts = self.data.get("notes_conflicts", [])
            self.data["notes_conflicts"] = []
        made = []
        for conflict in conflicts:
            if conflict["page_id"] == self.MAIN_NOTES_PAGE:
                base = "Notes"
            else:
                page = self._find_note_page(conflict["page_id"])
                base = page["title"] if page else (conflict.get("title") or "Page")
            title = f"{base} (other device, {conflict['at'][11:16]})"
            page_id = self.add_note_page(title)
            self.set_page_html(page_id, conflict["html"] or "")
            made.append(title)
        if conflicts:
            self.save()
        return made

    def outbox_note_pages_unsupported(self):
        """Called by the sync thread when the cloud schema has no notes
        pages yet (schema.sql not re-run): page edits are dropped from the
        queue instead of holding everything else up, and all pages are
        queued again on the next start."""
        with self._lock:
            self.data["notes_pages_synced"] = False

    def add_note_page(self, title):
        page = {"id": uuid.uuid4().hex[:8], "title": title, "html": ""}
        self.data.setdefault("notes_pages", []).append(page)
        self._enqueue_page(page)
        self.save()
        return page["id"]

    def rename_note_page(self, page_id, title):
        page = self._find_note_page(page_id)
        if page is not None:
            page["title"] = title
            self._enqueue_page(page)
            self.save()

    def remove_note_page(self, page_id):
        """Returns (page, index) for an undo, or None (the main page can't go)."""
        pages = self.data.get("notes_pages", [])
        for index, page in enumerate(pages):
            if page["id"] == page_id:
                del pages[index]
                if self.data.get("notes_current_page") == page_id:
                    self.data["notes_current_page"] = self.MAIN_NOTES_PAGE
                self._enqueue("note_page_remove", {"id": page_id}, key=f"page:{page_id}")
                self.save()
                return page, index
        return None

    def restore_note_page(self, page, index):
        pages = self.data.setdefault("notes_pages", [])
        pages.insert(min(index, len(pages)), page)
        self._enqueue_page(page)
        self._enqueue_page_order()
        self.save()

    def move_note_page(self, page_id, new_index):
        """new_index counts only the extra pages (the main page stays first)."""
        pages = self.data.get("notes_pages", [])
        page = self._find_note_page(page_id)
        if page is None:
            return
        pages.remove(page)
        pages.insert(max(0, min(new_index, len(pages))), page)
        self._enqueue_page_order()
        self.save()

    def get_current_note_page(self):
        page_id = self.data.get("notes_current_page", self.MAIN_NOTES_PAGE)
        return page_id if page_id == self.MAIN_NOTES_PAGE or self._find_note_page(page_id) else self.MAIN_NOTES_PAGE

    def set_current_note_page(self, page_id):
        self.data["notes_current_page"] = page_id
        self.save()

    def set_notes(self, text):
        self.data["notes"] = text
        self._enqueue("set_notes", {"text": text}, key="notes")
        self.save()

    # ---------- Checklist ----------
    def _roll_recurring_tasks(self):
        """Un-checks tasks whose period has rolled over: daily tasks each new
        day; a "weekday" task (e.g. "take out the trash" pinned to your
        collection day) whenever that weekday comes around; a "weekly" task
        when the quest week rolls over (Tuesday); a "monthly" task when its
        day of the month comes round. "once" tasks never reset. A task that
        resets un-ticks its subtasks too."""
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
                or (recurrence == "monthly" and task_dates.monthly_needs_reset(last, task.get("month_day"), today_date))
            )
            if should_reset and task.get("completed_today"):
                task["completed_today"] = False
                for sub in task.get("subtasks") or []:
                    sub["done"] = False
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
        self._roll_shared_tasks()
        self._ensure_daily_quests()
        self._ensure_weekly_quests()
        return True

    def _find_task(self, task_id):
        return next((t for t in self.data["checklist"] if t["id"] == task_id), None)

    def add_task(self, text, recurrence="daily", reminder_time=None, weekday=None, source="checklist",
                 month_day=None, due_date=None):
        month_day, due_date = task_dates.normalize(recurrence, month_day, due_date)
        task = {
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "recurrence": recurrence,  # "daily", "weekly", "once", "weekday", "monthly"
            "last_completed": None,
            "completed_today": False,
            "reminder_time": reminder_time,  # "HH:MM" or None
            "weekday": weekday,  # 0=Monday..6=Sunday, only meaningful for recurrence="weekday"; local-only, doesn't sync
            "last_reminded": None,  # date isoformat, so a reminder fires at most once/day (local-only)
            "reminder_every_h": None,  # hours between repeating reminders, or None (local-only, doesn't sync)
            "last_reminded_at": None,  # datetime isoformat the repeating reminder last fired/was set (local-only)
            "snoozed_until": None,  # datetime isoformat a snoozed reminder comes back (local-only)
            "awarded_on": None,  # last date this task paid out XP, see gamification.task_already_awarded
            "source": source,  # "checklist" (default) or "wishlist" (pushed from Deckbuilder)
            "month_day": month_day,  # 1..31 for a "monthly" task
            "due_date": due_date,  # "YYYY-MM-DD" for a "once" task, or None
            "note": "",
            "subtasks": [],  # [{id, text, done}]
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
                "month_day": task.get("month_day"), "due_date": task.get("due_date"),
            })
            if task.get("note") or task.get("subtasks"):  # e.g. a removal being undone
                self._enqueue_task_details(task)

    def rename_task(self, task_id, text):
        task = self._find_task(task_id)
        if task is None:
            return
        task["text"] = text
        if task["recurrence"] != "weekday":
            self._enqueue("rename_task", {"id": task_id, "text": text})
        self.save()

    def set_task_due_date(self, task_id, due_date):
        """A one-off task's due date ("YYYY-MM-DD"), or None to clear it."""
        task = self._find_task(task_id)
        if task is None or task["recurrence"] != "once":
            return
        task["due_date"] = task_dates.normalize("once", None, due_date)[1]
        task["last_reminded"] = None  # a daily-time reminder may fire on the new date
        self._enqueue("set_task_due_date", {"id": task_id, "due_date": task["due_date"]}, key=f"due:{task_id}")
        self.save()

    def set_task_note(self, task_id, note):
        task = self._find_task(task_id)
        if task is None:
            return
        task["note"] = note
        self._enqueue_task_details(task)
        self.save()

    def add_subtask(self, task_id, text):
        task = self._find_task(task_id)
        if task is None:
            return None
        sub = {"id": uuid.uuid4().hex[:8], "text": text, "done": False}
        task.setdefault("subtasks", []).append(sub)
        self._enqueue_task_details(task)
        self.save()
        return sub

    def rename_subtask(self, task_id, sub_id, text):
        self._edit_subtasks(task_id, lambda subs: [dict(s, text=text) if s["id"] == sub_id else s for s in subs])

    def remove_subtask(self, task_id, sub_id):
        self._edit_subtasks(task_id, lambda subs: [s for s in subs if s["id"] != sub_id])

    def move_subtask(self, task_id, sub_id, offset):
        def move(subs):
            index = next((i for i, s in enumerate(subs) if s["id"] == sub_id), None)
            if index is None or not 0 <= index + offset < len(subs):
                return subs
            subs.insert(index + offset, subs.pop(index))
            return subs
        self._edit_subtasks(task_id, move)

    def _edit_subtasks(self, task_id, change):
        task = self._find_task(task_id)
        if task is None:
            return
        task["subtasks"] = change(list(task.get("subtasks") or []))
        self._enqueue_task_details(task)
        self.save()

    def set_subtask_done(self, task_id, sub_id, done):
        """Ticks one subtask. Sent on its own (not the whole list), so ticking
        different steps on two devices at once can't undo either."""
        task = self._find_task(task_id)
        sub = next((s for s in (task or {}).get("subtasks") or [] if s["id"] == sub_id), None)
        if sub is None:
            return
        sub["done"] = bool(done)
        if task["recurrence"] != "weekday":
            self._enqueue("set_subtask_done", {"id": task_id, "sub_id": sub_id, "done": bool(done)})
        self.save()

    def _enqueue_task_details(self, task):
        if task["recurrence"] != "weekday":
            self._enqueue("set_task_details", {
                "id": task["id"], "note": task.get("note") or "",
                "subtasks": [{"id": s["id"], "text": s["text"]} for s in task.get("subtasks") or []],
            }, key=f"details:{task['id']}")

    def set_task_reminder(self, task_id, reminder_time, every_hours=None):
        """reminder_time: "HH:MM" string for a once-a-day reminder, or
        every_hours: an int for a reminder repeating every that many hours
        (counted from now). Neither clears the task's reminder. A task has
        at most one kind. Both sync (the repeating one from v2.17; an older
        cloud just gets the time cleared, see SyncEngine)."""
        task = self._find_task(task_id)
        if task is None:
            return
        if every_hours:
            reminder_time = None
        task["reminder_time"] = reminder_time
        task["last_reminded"] = None
        task["reminder_every_h"] = every_hours or None
        task["last_reminded_at"] = datetime.now().isoformat(timespec="seconds") if every_hours else None
        if task["recurrence"] != "weekday":
            self._enqueue("set_task_reminder_mode",
                          {"id": task_id, "reminder_time": reminder_time, "every_h": every_hours or None})
        self.save()

    def get_reminder_settings(self):
        return dict(self.data.get("reminders", {}))

    def set_reminder_settings(self, **changes):
        settings = self.data.setdefault("reminders", {})
        settings.update(changes)
        self._enqueue("set_reminder_settings", {
            "quiet_enabled": bool(settings.get("quiet_enabled")),
            "quiet_start": settings.get("quiet_start", "22:00"),
            "quiet_end": settings.get("quiet_end", "08:00"),
        }, key="reminders")
        self.save()

    def _in_quiet_hours(self, now):
        r = self.data.get("reminders", {})
        if not r.get("quiet_enabled"):
            return False
        start, end, hm = r.get("quiet_start", "22:00"), r.get("quiet_end", "08:00"), now.strftime("%H:%M")
        if start == end:
            return False
        if start < end:
            return start <= hm < end
        return hm >= start or hm < end  # spans midnight

    def snooze_task_reminder(self, task_id, minutes):
        """Brings this task's reminder back in `minutes` (once), on top of
        whatever regular reminder it has."""
        task = self._find_task(task_id)
        if task is None:
            return
        until = datetime.now() + timedelta(minutes=minutes)
        task["snoozed_until"] = until.isoformat(timespec="seconds")
        if task["recurrence"] != "weekday":
            self._enqueue("snooze_task", {"id": task_id, "until": until.astimezone().isoformat(timespec="seconds")})
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
        changed = False
        quiet = self._in_quiet_hours(now)
        for task in self.data.get("checklist", []):
            snoozed = task.get("snoozed_until")
            if snoozed:
                if task.get("completed_today"):
                    task["snoozed_until"] = None  # done: nothing left to come back for
                    changed = True
                elif now.isoformat(timespec="seconds") >= snoozed:
                    task["snoozed_until"] = None
                    # The cloud clears its copy once it has pushed it; until a
                    # pull shows that, don't let the old value fire again here.
                    task["snooze_fired_for"] = snoozed
                    due.append(task)
                    continue
                else:
                    continue  # its regular reminder waits until the snooze is over
            if not task_dates.reminds_today(task, now.date()):
                continue  # a specific-day / monthly task only nags on its day, a dated one from its date
            every_h = task.get("reminder_every_h")
            if every_h:
                if task.get("completed_today") or quiet:
                    continue
                try:
                    last = datetime.fromisoformat(task.get("last_reminded_at") or "")
                except ValueError:
                    last = None
                # A clock moved backwards would otherwise silence it for good.
                if last is None or last > now or now - last >= timedelta(hours=every_h):
                    task["last_reminded_at"] = now.isoformat(timespec="seconds")
                    due.append(task)
                continue
            reminder_time = task.get("reminder_time")
            if not reminder_time or task.get("completed_today"):
                continue
            if task.get("last_reminded") == today:
                continue
            if current_hm >= reminder_time:
                task["last_reminded"] = today
                due.append(task)
        if due or changed:
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
        if done and not gamification.task_already_awarded(task["recurrence"], task.get("awarded_on"), today,
                                                           task.get("month_day")):
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

    def get_timer_state(self):
        return self.data.get("timer_state")

    def set_timer_state(self, state):
        self.data["timer_state"] = state
        self.save()

    def get_focus_task(self):
        """The task chosen on the Pomodoro tab, if it still exists."""
        task_id = self.data.get("focus_task")
        return self._find_task(task_id) if task_id else None

    def set_focus_task(self, task_id):
        self.data["focus_task"] = task_id
        self.save()

    def pending_once_tasks(self):
        """One-time checklist tasks not done yet: what the timer's 🎲 picks from."""
        return [t for t in self.get_checklist()
                if t.get("source", "checklist") == "checklist" and t["recurrence"] == "once"
                and not t.get("completed_today")]

    def pick_random_focus_task(self, rng=random):
        """Makes a random pending one-time task the timer's focus and returns
        it (None when there are none). Avoids the current one while there's
        anything else, so picking again always changes it."""
        tasks = self.pending_once_tasks()
        current = self.data.get("focus_task")
        others = [t for t in tasks if t["id"] != current]
        if not tasks:
            return None
        task = rng.choice(others or tasks)
        self.set_focus_task(task["id"])
        return task

    def get_focus_auto_pick(self):
        return bool(self.data.get("focus_auto_pick", True))

    def set_focus_auto_pick(self, enabled):
        self.data["focus_auto_pick"] = bool(enabled)
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
            self.data["notes_pages_synced"] = False  # bring this computer's pages to that account
        self.save()
        self._queue_unsynced_pages()
        self.sync.reconfigure()

    def _apply_remote_state(self, remote, keep_notes=False):
        """Overwrites the local cache with the server's copy of notes,
        checklist, and gamification state (server is authoritative once
        connected)."""
        if not keep_notes:
            self.data["notes"] = remote["notes"]
            # Only with the text: while you're typing, the revision stays at
            # the one your text is based on, so the upload is conflict-checked.
            if "notes_rev" in remote:
                self.data["notes_rev"] = remote["notes_rev"]
        if isinstance(remote.get("reminders"), dict):
            self.data.setdefault("reminders", {}).update(
                {k: remote["reminders"][k] for k in ("quiet_enabled", "quiet_start", "quiet_end") if k in remote["reminders"]})
        self._apply_remote_pages(remote, keep_notes)
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
                **self._remote_reminder_fields(t, local_tasks.get(t["id"]) or {}),
                "focus_pomodoros": (local_tasks.get(t["id"]) or {}).get("focus_pomodoros", 0),
                "focus_min": (local_tasks.get(t["id"]) or {}).get("focus_min", 0),
                "awarded_on": t.get("awarded_on"),
                "source": t.get("source", "checklist"),
                **self._remote_detail_fields(t, local_tasks.get(t["id"]) or {}),
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

    @staticmethod
    def _cloud_time_to_local(value):
        """A timestamptz from the cloud as the naive local ISO string this
        file uses for times (or None)."""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed.isoformat(timespec="seconds")

    @staticmethod
    def _remote_detail_fields(remote_task, local_task):
        """Monthly day, due date, note and subtasks from a pull. A cloud older
        than v2.18 has none of them; then this computer's own are kept."""
        if "subtasks" not in remote_task:
            return {k: local_task.get(k, default) for k, default in
                    (("month_day", None), ("due_date", None), ("note", ""), ("subtasks", []))}
        subtasks = remote_task.get("subtasks")
        return {
            "month_day": remote_task.get("month_day"),
            "due_date": remote_task.get("due_date"),
            "note": remote_task.get("note") or "",
            "subtasks": [{"id": str(s.get("id")), "text": str(s.get("text", "")), "done": bool(s.get("done"))}
                         for s in subtasks if isinstance(s, dict) and s.get("id")]
            if isinstance(subtasks, list) else [],
        }

    def _remote_reminder_fields(self, remote_task, local_task):
        """Every-N-hours and snooze for a task from a pull. A v2.17 cloud
        sends both (and wins); an older one has neither, and then a time
        set elsewhere (e.g. the phone) replaces a local repeating reminder."""
        last_at = local_task.get("last_reminded_at")
        if "reminder_every_h" in remote_task:
            every_h = remote_task.get("reminder_every_h")
            snoozed = self._cloud_time_to_local(remote_task.get("snoozed_until"))
            if snoozed and snoozed == local_task.get("snooze_fired_for"):
                snoozed = None  # already reminded here; the cloud just hasn't caught up
            if every_h and not last_at:
                last_at = datetime.now().isoformat(timespec="seconds")  # set on another device
        else:
            every_h = None if remote_task.get("reminder_time") else local_task.get("reminder_every_h")
            snoozed = local_task.get("snoozed_until")
        return {
            "reminder_every_h": every_h,
            "last_reminded_at": last_at if every_h else None,
            "snoozed_until": snoozed,
            "snooze_fired_for": local_task.get("snooze_fired_for"),
        }

    def _apply_remote_pages(self, remote, keep_notes):
        """Pages from the cloud replace the local ones, once this computer's
        pages have been uploaded (or a pull could delete pages that only
        exist here). Absent from an older cloud schema: then pages stay as
        they are. `keep_notes`: the open page is being typed in, keep it."""
        if "notes_pages" not in remote:
            return
        if not self.data.get("notes_pages_synced"):
            self._queue_unsynced_pages()  # the cloud just gained pages support
            return
        open_page = self._find_note_page(self.data.get("notes_current_page")) if keep_notes else None
        pages = [{"id": p["id"], "title": p["title"], "html": p.get("html") or "", "rev": p.get("rev")}
                 for p in remote["notes_pages"]]
        if open_page is not None:
            pages = [open_page if p["id"] == open_page["id"] else p for p in pages]
        self.data["notes_pages"] = pages

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

    def _focus_log_bump(self, task, minutes):
        log = self.data.setdefault("focus_log", {})
        row = log.setdefault(date.today().isoformat(), {}).setdefault(
            task["id"], {"text": task["text"], "pomodoros": 0, "focus_min": 0})
        row["text"] = task["text"]
        row["pomodoros"] += 1
        row["focus_min"] += minutes
        cutoff = (date.today() - timedelta(days=HISTORY_KEEP_DAYS)).isoformat()
        for day in [d for d in log if d < cutoff]:
            del log[day]

    def get_week_focus_by_task(self):
        """This quest week's (since Tuesday) focus per task, most first:
        [{"id", "text", "pomodoros", "focus_min"}]. Uses the task's current
        name when it still exists."""
        start = gamification.week_start_for(date.today()).isoformat()
        totals = {}
        for day, tasks in self.data.get("focus_log", {}).items():
            if day < start:
                continue
            for task_id, row in tasks.items():
                total = totals.setdefault(task_id, {"id": task_id, "text": row["text"], "pomodoros": 0, "focus_min": 0})
                total["pomodoros"] += row["pomodoros"]
                total["focus_min"] += row["focus_min"]
        for total in totals.values():
            task = self._find_task(total["id"])
            if task is not None:
                total["text"] = task["text"]
        return sorted(totals.values(), key=lambda r: (-r["focus_min"], -r["pomodoros"], r["text"]))

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

    # ---------- Updates (see update_checker.py) ----------
    def get_update_auto_check(self):
        return bool(self.data.get("updates", {}).get("auto_check", True))

    def set_update_auto_check(self, enabled):
        self.data.setdefault("updates", {})["auto_check"] = bool(enabled)
        self.save()

    def get_update_notified(self):
        return self.data.get("updates", {}).get("notified_version")

    def set_update_notified(self, version):
        self.data.setdefault("updates", {})["notified_version"] = version
        self.save()

    # ---------- Household (shared list; see schema.sql) ----------
    def get_household(self):
        return self.data.get("household")

    def set_household(self, household):
        """Stores the household returned by the create/join calls (or None
        after leaving)."""
        self.data["household"] = household
        self._bump_rev()
        self.save()

    def get_shared_notify(self):
        return bool(self.data.get("shared_notify", True))

    def set_shared_notify(self, enabled):
        self.data["shared_notify"] = bool(enabled)
        self.save()

    def household_my_name(self):
        household = self.get_household() or {}
        for member in household.get("members", []):
            if member.get("is_me"):
                return member.get("name") or "Me"
        return "Me"

    @staticmethod
    def _shared_schedule_of(task):
        return shared_schedule.normalize({k: task.get(k) for k in shared_schedule.DEFAULT_SCHEDULE})

    def _find_shared(self, task_id):
        return next((t for t in (self.get_household() or {}).get("tasks", []) if t["id"] == task_id), None)

    def shared_add_task(self, text, schedule=None):
        household = self.get_household()
        if not household:
            return None
        schedule = shared_schedule.normalize(schedule)
        task = {"id": uuid.uuid4().hex[:12], "text": text, "done": False, "done_by_name": None, "done_at": None,
                **schedule}
        household["tasks"].append(task)
        args = {"id": task["id"], "text": text}
        if not shared_schedule.is_plain(schedule):
            args["schedule"] = schedule  # left out for plain items so they still upload to an older cloud schema
        self._enqueue("shared_add", args)
        self.save()
        return task

    def shared_set_schedule(self, task_id, schedule):
        task = self._find_shared(task_id)
        if task is None:
            return
        schedule = shared_schedule.normalize(schedule)
        task.update(schedule)
        self.data.get("shared_reminded", {}).pop(task_id, None)  # a new time may fire again
        self._enqueue("shared_schedule", {"id": task_id, "schedule": schedule})
        self.save()

    def shared_reorder(self, ordered_ids):
        """Reorders the shared list to match `ordered_ids`; any task not
        listed (e.g. one your partner just added) keeps its place at the end."""
        household = self.get_household()
        if not household:
            return
        by_id = {t["id"]: t for t in household["tasks"]}
        listed = [by_id[i] for i in ordered_ids if i in by_id]
        household["tasks"] = listed + [t for t in household["tasks"] if t["id"] not in ordered_ids]
        self._enqueue("shared_reorder", {"ids": [t["id"] for t in household["tasks"]]}, key="shared_reorder")
        self.save()

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
        """Removes ticked one-off items. Repeating items stay: they un-tick
        themselves when they come due again."""
        household = self.get_household()
        if household:
            household["tasks"] = [
                t for t in household["tasks"]
                if not t["done"] or t.get("recurrence", "once") != "once"
            ]
        self._enqueue("shared_clear_done", {}, key="shared_clear_done")
        self.save()

    def _roll_shared_tasks(self):
        """Un-ticks repeating shared items that have come due again since
        they were ticked. The cloud does the same on every pull (see
        roll_shared_tasks in schema.sql), so nothing is queued here -- this
        just keeps an offline desktop right at midnight."""
        household = self.get_household()
        if not household:
            return
        today = date.today()
        changed = False
        for task in household.get("tasks", []):
            if not task.get("done") or task.get("recurrence", "once") == "once":
                continue
            done_date = shared_schedule.parse_timestamp(task.get("done_at"))
            if shared_schedule.needs_reset(self._shared_schedule_of(task), done_date, today):
                task["done"] = False
                task["done_by_name"] = None
                task["done_at"] = None
                changed = True
        if changed:
            self.save()

    def check_due_shared_reminders(self):
        """Shared items whose date/time has arrived and that aren't ticked,
        once per occurrence (tracked in `shared_reminded`, which is kept apart
        from the household cache so a cloud pull can't reset it)."""
        household = self.get_household()
        reminded = self.data.setdefault("shared_reminded", {})
        live = {t["id"] for t in (household or {}).get("tasks", [])}
        stale = [i for i in reminded if i not in live]
        for task_id in stale:
            del reminded[task_id]
        now = datetime.now()
        due = []
        for task in (household or {}).get("tasks", []):
            if task.get("done"):
                continue
            occurrence = shared_schedule.reminder_occurrence(self._shared_schedule_of(task), now)
            if occurrence and reminded.get(task["id"]) != occurrence:
                reminded[task["id"]] = occurrence
                due.append(task)
        if due or stale:
            self.save()
        return due

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

    def record_pomodoro_completed(self, minutes=None, task_id=None):
        """Call once per completed work session (not for a skipped one).
        `minutes` is the session length, for the focus-time history.
        `task_id`: the checklist task the session was spent on, if any; its
        local-only focus_pomodoros/focus_min totals go up.
        Returns a dict describing what was earned, for the UI to celebrate."""
        if minutes is None:
            minutes = self.get_pomodoro_settings().get("work_min", 25)
        task = self._find_task(task_id) if task_id else None
        if task is not None:
            task["focus_pomodoros"] = task.get("focus_pomodoros", 0) + 1
            task["focus_min"] = task.get("focus_min", 0) + minutes
            self._focus_log_bump(task, minutes)
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
