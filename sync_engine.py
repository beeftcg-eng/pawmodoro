"""
sync_engine.py - Background worker that keeps Pawmodoro in step with the
Supabase backend without ever making the UI wait on the network.

How it fits together:

  * Every local edit (a note, a ticked task, a finished pomodoro, a shared
    list item...) is applied to the local data immediately by Storage, and
    ALSO appended to a persistent outbox (storage.data["sync_outbox"]).
  * This engine's worker thread drains that outbox in order, one RPC at a
    time. If the network is down or the cloud schema is outdated, the queue
    simply waits (it survives restarts, since it lives in data.json).
    A request the server rejects as *wrong* (e.g. "task not found") is
    dropped instead, so one bad entry can never wedge the queue.
  * Once the outbox is empty, it pulls the server's state and hands it to
    `on_pulled` -- but only if nothing changed locally since the pull began,
    so a slow pull can never overwrite an edit you just made. (The final
    check is repeated on the UI thread in Storage.adopt_remote_state.)

Storage is only ever touched from here through its small, lock-protected
outbox/token methods; everything else stays on the UI thread.
"""
import threading
import time
import traceback
from datetime import datetime

from supabase_sync import SupabaseSync, SyncError

POLL_SECONDS = 5           # how often to pull while things are healthy
SLOW_RETRY_SECONDS = 60    # when the session is dead / schema is outdated
HOUSEHOLD_PROBE_SECONDS = 60  # how often to look for a household created elsewhere

STATUS_TEXT = {
    "off": "Cloud sync is off",
    "connecting": "Connecting…",
    "online": "Synced",
    "offline": "Offline — changes are saved here and will sync when the connection is back",
    "auth": "Signed out of the cloud — open Cloud Sync and log in again (nothing is lost)",
    "schema": "The cloud database needs updating — re-run supabase/schema.sql in the Supabase SQL Editor (see MOBILE_SYNC.md)",
}


def local_utc_offset_minutes():
    return int(datetime.now().astimezone().utcoffset().total_seconds() // 60)


class SyncEngine:
    def __init__(self, storage):
        self.storage = storage
        self.client = None
        self.status = "off"
        self.last_error = ""
        # Called from the worker thread as on_pulled(remote, household, rev);
        # `household` is False when it wasn't checked this round, None when
        # the account isn't in one, else the household dict. MainWindow points
        # this at a Qt signal so delivery lands on the UI thread.
        self.on_pulled = None

        self._io_lock = threading.RLock()  # one network request at a time
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._last_pull = 0.0
        self._tz_sent = None
        self._tz_retry_at = 0.0
        self._household_supported = True
        self._next_household_probe = 0.0

    # ---------- lifecycle ----------

    def start(self):
        if self._thread is None:
            if self.storage.sync_configured():
                self.status = "connecting"
            self._thread = threading.Thread(target=self._run, name="pawmodoro-sync", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def wake(self):
        """Ask for a sync cycle right away (after a local edit)."""
        self._wake.set()

    def reconfigure(self):
        """Sync settings changed (connected / disconnected / different
        account): forget the current session and start fresh."""
        with self._io_lock:
            self.client = None
            self._tz_sent = None
            self._household_supported = True
            self._next_household_probe = 0.0
            self._last_pull = 0.0
        self.status = "connecting" if self.storage.sync_configured() else "off"
        self.wake()

    def pending_count(self):
        return self.storage.outbox_len()

    def status_text(self):
        text = STATUS_TEXT.get(self.status, self.status)
        pending = self.pending_count()
        if pending and self.status != "off":
            text += f" ({pending} change{'s' if pending != 1 else ''} waiting to upload)"
        return text

    # ---------- direct calls from the UI (user-initiated, rare) ----------

    def call_now(self, fn):
        """Runs fn(client) right now on the calling thread (e.g. creating or
        joining a household). Raises SyncError if not connected or the call
        fails; the caller reports it to the user."""
        with self._io_lock:
            if self.client is None:
                cfg = self.storage.get_sync_config()
                if not self.storage.sync_configured():
                    raise SyncError("Cloud sync isn't turned on. Click ☁️ Sync and log in first.")
                if not self._connect(cfg):
                    raise SyncError(self.last_error or "Couldn't reach the cloud.")
            try:
                return fn(self.client)
            finally:
                self._persist_token()

    # ---------- worker ----------

    def _run(self):
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception:  # noqa: BLE001 - the sync thread must never die
                traceback.print_exc()
            delay = SLOW_RETRY_SECONDS if self.status in ("auth", "schema") else POLL_SECONDS
            self._wake.wait(delay)
            self._wake.clear()

    def _cycle(self):
        if not self.storage.sync_configured():
            with self._io_lock:
                self.client = None
            self.status = "off"
            return

        with self._io_lock:
            if self.client is None:
                self.status = "connecting"
                if not self._connect(self.storage.get_sync_config()):
                    return
            self._ensure_tz()

        if not self._flush():
            return

        # A wake-up caused by a local edit shouldn't also trigger a pull
        # every time; only pull on the regular poll interval.
        if time.monotonic() - self._last_pull < POLL_SECONDS - 0.5:
            return
        self._pull()

    def _connect(self, cfg):
        client = SupabaseSync(cfg["url"], cfg["anon_key"], refresh_token=cfg["refresh_token"])
        try:
            client.refresh_session()
        except SyncError as e:
            self._note_failure(e)
            if e.status in (400, 401, 403):  # the refresh token itself was refused
                self.status = "auth"
            return False
        self.client = client
        # Supabase rotates the refresh token on every use; the one we just
        # spent is dead, so persist the new one immediately.
        self._persist_token()
        self.status = "online"
        return True

    def _persist_token(self):
        if self.client is not None:
            self.storage.persist_sync_token(self.client.refresh_token)

    def _note_failure(self, error):
        self.last_error = str(error)
        if error.auth:
            self.status = "auth"
        elif error.schema_outdated:
            self.status = "schema"
        else:
            self.status = "offline"

    def _ensure_tz(self):
        """Tell the server our UTC offset so "today" rolls over at OUR
        midnight (see user_today() in schema.sql). Best-effort."""
        minutes = local_utc_offset_minutes()
        if minutes == self._tz_sent or time.monotonic() < self._tz_retry_at:
            return
        try:
            self.client.set_tz_offset(minutes)
            self._tz_sent = minutes
        except SyncError as e:
            if e.auth:
                self._note_failure(e)
            self._tz_retry_at = time.monotonic() + 120

    def _flush(self):
        """Sends queued changes in order. True when the queue is empty."""
        while True:
            op = self.storage.outbox_peek()
            if op is None:
                return True
            try:
                with self._io_lock:
                    self._execute(op)
            except SyncError as e:
                if e.transient:
                    self.storage.outbox_release()
                    self._note_failure(e)
                    return False
                print(f"[sync] dropping {op['op']} ({e})")
                self.storage.outbox_done(op["seq"])
                continue
            self.storage.outbox_done(op["seq"])
            self._persist_token()
            self.status = "online"

    def _execute(self, op):
        a = op["args"]
        c = self.client
        name = op["op"]
        if name == "set_notes":
            c.set_notes(a["text"])
        elif name == "add_task":
            c.add_task(a["text"], a["recurrence"], a.get("reminder_time"), a.get("source", "checklist"), a["id"])
        elif name == "remove_task":
            c.remove_task(a["id"])
        elif name == "rename_task":
            c.rename_task(a["id"], a["text"])
        elif name == "set_task_reminder":
            c.set_task_reminder(a["id"], a["reminder_time"])
        elif name == "reorder_tasks":
            c.reorder_tasks(a["ids"])
        elif name == "complete_task":
            c.complete_task(a["id"], a["done"])
        elif name == "pomodoro":
            c.record_pomodoro_completed(a.get("minutes", 25))
        elif name == "break":
            c.record_break_completed()
        elif name == "shared_add":
            c.shared_add_task(a["text"], a["id"])
        elif name == "shared_done":
            c.shared_set_done(a["id"], a["done"])
        elif name == "shared_remove":
            c.shared_remove_task(a["id"])
        elif name == "shared_clear_done":
            c.shared_clear_done()
        else:
            print(f"[sync] unknown queued operation {name!r}; dropping it")

    def _pull(self):
        rev = self.storage.rev
        try:
            with self._io_lock:
                remote = self.client.sync_pull()
        except SyncError as e:
            self._note_failure(e)
            return
        household = self._pull_household()
        self._last_pull = time.monotonic()
        self._persist_token()
        self.status = "online"
        if self.on_pulled is not None:
            self.on_pulled(remote, household, rev)

    def _pull_household(self):
        if not self._household_supported:
            return False
        in_household = self.storage.get_household() is not None
        now = time.monotonic()
        if not in_household and now < self._next_household_probe:
            return False
        self._next_household_probe = now + HOUSEHOLD_PROBE_SECONDS
        try:
            with self._io_lock:
                return self.client.household_pull()
        except SyncError as e:
            if e.schema_outdated:
                # Older cloud schema without the household feature: the rest
                # of sync works fine, so just stop asking.
                self._household_supported = False
            return False
