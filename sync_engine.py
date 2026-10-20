"""
sync_engine.py - Background worker that keeps Pawmodoro in step with the
Supabase backend without ever making the UI wait on the network.

How it fits together:

  * Every local edit (a note, a ticked task, a finished pomodoro, a shared
    list item...) is applied to the local data immediately by Storage, and
    ALSO appended to a persistent outbox (storage.data["sync_outbox"]).
  * This engine's worker thread drains that outbox in order, one RPC at a
    time. If the network is down or the cloud schema is older than this app
    (schema_version() below SCHEMA_VERSION), the queue simply waits (it survives restarts, since it lives in data.json).
    A request the server rejects as *wrong* (e.g. "task not found") is
    dropped instead, so one bad entry can never wedge the queue.
  * Once the outbox is empty, it pulls the server's state and hands it to
    `on_pulled` -- but only if nothing changed locally since the pull began,
    so a slow pull can never overwrite an edit you just made. (The final
    check is repeated on the UI thread in Storage.adopt_remote_state.)

Storage is only ever touched from here through its small, lock-protected
outbox/token methods; everything else stays on the UI thread.
"""
import json
import os
import threading
import time
import traceback
from datetime import datetime

from supabase_sync import SCHEMA_VERSION, SupabaseSync, SyncError

POLL_SECONDS = 5           # how often to pull while things are healthy
BACKGROUND_POLL_SECONDS = 60  # ...and while no Pawmodoro window is on screen (sitting in the tray)
SLOW_RETRY_SECONDS = 60    # when the session is dead / schema is outdated
HOUSEHOLD_PROBE_SECONDS = 60  # how often to look for a household created elsewhere

STATUS_TEXT = {
    "off": "Cloud sync is off",
    "connecting": "Connecting…",
    "online": "Synced",
    "offline": "Offline — changes are saved here and will sync when the connection is back",
    "auth": "Signed out of the cloud — open Cloud Sync and log in again (nothing is lost)",
    "schema": ("The cloud database is older than this app — re-run supabase/schema.sql in the Supabase "
               "SQL Editor (see MOBILE_SYNC.md). Changes are saved here and will sync once it's updated"),
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
        self._next_household_probe = 0.0
        # Polling only sends what changed (sync_pull_if_changed). Storage
        # sets `applied` = (state fingerprint, household fingerprint, rev)
        # when it applies a pull; the fingerprints are only offered while
        # storage.rev still equals that rev, so after any local edit or
        # upload the next pull is a full one.
        self.applied = (None, None, None)
        # Set from the UI thread: True while no window is showing. Pulls then
        # slow right down; local edits still upload immediately (wake()).
        self.background = False

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

    def set_background(self, background):
        """A window appearing again gets a fresh pull right away."""
        if self.background and not background:
            self._last_pull = 0.0
            self.wake()
        self.background = background

    def _poll_seconds(self):
        return BACKGROUND_POLL_SECONDS if self.background else POLL_SECONDS

    def reconfigure(self):
        """Sync settings changed (connected / disconnected / different
        account): forget the current session and start fresh."""
        with self._io_lock:
            self.client = None
            self._tz_sent = None
            self.applied = (None, None, None)
            self._next_household_probe = 0.0
            self._last_pull = 0.0
        self.status = "connecting" if self.storage.sync_configured() else "off"
        self.wake()

    def pending_count(self):
        return self.storage.outbox_len()

    def needs_attention(self):
        """Something only the user can fix (log in again, update the cloud)."""
        return self.status in ("auth", "schema")

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
            delay = SLOW_RETRY_SECONDS if self.status in ("auth", "schema") else self._poll_seconds()
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
        if time.monotonic() - self._last_pull < self._poll_seconds() - 0.5:
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
        if not self._schema_current():
            self.client = None  # asked again on the next (slow) retry
            return False
        self.status = "online"
        return True

    def _schema_current(self):
        """An older cloud schema (or one from before schema_version(),
        v2.20) doesn't sync at all until schema.sql is re-run: the queue
        keeps every change meanwhile."""
        try:
            version = self.client.schema_version()
        except SyncError as e:
            if not e.schema_outdated:
                self._note_failure(e)
                return False
            version = 0
        if int(version or 0) < SCHEMA_VERSION:
            self.last_error = f"cloud schema {version or 'unversioned'}, this app needs {SCHEMA_VERSION}"
            self.status = "schema"
            return False
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
            self._save_page_checked(self.storage.MAIN_NOTES_PAGE, None, a["text"])
        elif name == "add_task":
            c.add_task(a["text"], a["recurrence"], a.get("reminder_time"), a.get("source", "checklist"), a["id"],
                       a.get("month_day"), a.get("due_date"), a.get("weekday"))
        elif name == "set_task_due_date":
            c.set_task_due_date(a["id"], a["due_date"])
        elif name == "set_task_details":
            c.set_task_details(a["id"], a["note"], a["subtasks"])
        elif name == "set_subtask_done":
            c.set_subtask_done(a["id"], a["sub_id"], a["done"])
        elif name == "remove_task":
            c.remove_task(a["id"])
        elif name == "rename_task":
            c.rename_task(a["id"], a["text"])
        elif name == "set_task_reminder":
            c.set_task_reminder(a["id"], a["reminder_time"])
        elif name == "set_task_reminder_mode":
            c.set_task_reminder_mode(a["id"], a.get("reminder_time"), a.get("every_h"))
        elif name == "snooze_task":
            c.snooze_task(a["id"], a["until"])
        elif name == "set_reminder_settings":
            c.set_reminder_settings(a["quiet_enabled"], a["quiet_start"], a["quiet_end"])
        elif name == "reorder_tasks":
            c.reorder_tasks(a["ids"])
        elif name == "complete_task":
            c.complete_task(a["id"], a["done"])
        elif name == "task_flag":
            c.set_task_completed_flag(a["id"], a["done"])
        elif name == "pomodoro":
            c.record_pomodoro_completed(a.get("minutes", 25), a.get("session"))
        elif name == "break":
            c.record_break_completed(a.get("session"))
        elif name == "timer_state":
            c.set_timer_state(a)
        elif name == "shared_add":
            c.shared_add_task(a["text"], a["id"], a.get("schedule"))
        elif name == "shared_schedule":
            c.shared_set_schedule(a["id"], a["schedule"])
        elif name == "shared_reorder":
            c.shared_reorder(a["ids"])
        elif name == "shared_done":
            c.shared_set_done(a["id"], a["done"])
        elif name == "shared_remove":
            c.shared_remove_task(a["id"])
        elif name == "shared_assign":
            c.shared_assign(a["id"], a["user_id"])
        elif name == "shared_clear_done":
            c.shared_clear_done()
        elif name == "note_page_set":
            self._save_page_checked(a["id"], a["title"], a["html"])
        elif name == "note_page_remove":
            c.remove_note_page(a["id"])
        elif name == "note_pages_reorder":
            c.reorder_note_pages(a["ids"])
        elif name == "event_save":
            try:
                c.save_event(a, a.get("shared"))
            except SyncError as e:
                if not e.transient:
                    self.storage.outbox_event_failed(a["id"], _readable(e))
                raise
        elif name == "event_remove":
            c.remove_event(a["id"])
        elif name == "attachment_add":
            self._upload_attachment(a)
        elif name == "attachment_remove":
            self._remove_attachment(a)
        else:
            print(f"[sync] unknown queued operation {name!r}; dropping it")

    def _upload_attachment(self, a):
        """Registers the file (which says where it goes), sends its bytes to
        Storage, then marks it done. Safe to repeat after a failure part way."""
        import storage  # the module, for where files are kept (imported late: it imports this one)
        local = storage.attachment_file(a["id"], a["file_name"])
        if not os.path.exists(local):
            print(f"[sync] attached file {a['file_name']!r} is gone; not uploading it")
            return
        c = self.client
        try:
            row = c.add_attachment(a["id"], a["item_kind"], a["item_id"], a["file_name"], a["mime"], a["size"])
            with open(local, "rb") as f:
                data = f.read()
            try:
                c.upload_file(row["path"], data, a["mime"])
            except SyncError as e:
                if e.status == 404:  # the schema ran, but the bucket couldn't be made
                    raise SyncError("the cloud has no attachments bucket (see MOBILE_SYNC.md)", status=400) from e
                raise
            c.attachment_uploaded(a["id"])
        except SyncError as e:
            if not e.transient:
                self.storage.outbox_attachment_failed(a["id"], _readable(e))
            raise
        self.storage.outbox_attachment_uploaded(a["id"], row["path"])

    def _remove_attachment(self, a):
        """Storage first: its policy needs the attachments row to still be there."""
        c = self.client
        path = a.get("path")
        if not path:  # removed before a pull said where it went: where add_attachment put it
            owner = f"h/{a['household_id']}" if a.get("household_id") else f"u/{c.user_id}"
            path = f"{owner}/{a['id']}"
        c.delete_file(path)
        c.remove_attachment(a["id"])

    def download(self, path, destination):
        """Fetches an attached file to `destination` (for opening it). Runs
        on the calling thread, which mustn't be the UI's. Raises SyncError."""
        data = self.call_now(lambda c: c.download_file(path))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        partial = destination + ".part"
        with open(partial, "wb") as f:
            f.write(data)
        os.replace(partial, destination)
        return destination

    def _save_page_checked(self, page_id, title, html):
        """Saves notes (page_id "main") or a page, refused by the cloud if
        another device saved it since this computer last saw it. Then their
        version is kept as a separate page (Storage.outbox_notes_conflict)
        and ours goes on top."""
        c = self.client
        main = page_id == self.storage.MAIN_NOTES_PAGE
        for _attempt in range(3):
            base = self.storage.outbox_base_rev(page_id)
            result = (c.set_notes_checked(html, base) if main
                      else c.set_note_page_checked(page_id, title, html, base))
            if result.get("ok"):
                self.storage.outbox_saved_rev(page_id, result.get("rev"))
                return
            print(f"[sync] {page_id} notes changed elsewhere; keeping both versions")
            self.storage.outbox_notes_conflict(
                page_id, result.get("title", title), result.get("notes" if main else "html", ""), result.get("rev"))
        raise SyncError("notes kept changing while saving", status=None)

    def _known_fingerprints(self, rev):
        state_fp, household_fp, applied_rev = self.applied
        return (state_fp, household_fp) if applied_rev == rev else (None, None)

    def _pull(self):
        rev = self.storage.rev
        state_fp, household_fp = self._known_fingerprints(rev)
        try:
            with self._io_lock:
                remote = self._pull_state(state_fp)
        except SyncError as e:
            self._note_failure(e)
            return
        household = self._pull_household(household_fp)
        self._last_pull = time.monotonic()
        self._persist_token()
        self.status = "online"
        # Nothing new since what's already applied: nothing to hand over.
        if remote is None and household is False:
            return
        if self.on_pulled is not None:
            self.on_pulled(remote, household, rev)

    def _pull_state(self, fingerprint):
        """The server's state, or None when it still matches `fingerprint`."""
        remote = self.client.sync_pull_if_changed(fingerprint)
        return None if remote.get("unchanged") else remote

    def _pull_household(self, fingerprint=None):
        """False when not checked (or unchanged since `fingerprint`), None
        when the account isn't in a household, else the household dict
        (with its "fingerprint")."""
        in_household = self.storage.get_household() is not None
        now = time.monotonic()
        if not in_household and now < self._next_household_probe:
            return False
        self._next_household_probe = now + HOUSEHOLD_PROBE_SECONDS
        try:
            with self._io_lock:
                pulled = self.client.household_pull_if_changed(fingerprint if in_household else None)
                if pulled.get("unchanged"):
                    return False
                household = pulled.get("household")
                if household is not None:
                    # taken back out by Storage.adopt_remote_state
                    household["fingerprint"] = pulled.get("fingerprint")
                return household
        except SyncError:
            return False


def _readable(error):
    """The message out of a PostgREST / Storage error ("400: {...}")."""
    text = str(error)
    try:
        body = json.loads(text.split(": ", 1)[1])
        return body.get("message") or body.get("error") or text
    except (IndexError, ValueError, AttributeError):
        return text
