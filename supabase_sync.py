"""
supabase_sync.py - Thin, defensive HTTP client for the Pawmodoro Supabase
backend (see pawmodoro-mobile/supabase/schema.sql on the Desktop for the
schema/RPC functions this calls). Uses stdlib urllib only, same pattern as
spotify_client.py, so no new dependency is needed.

Every network call has a short timeout and raises SyncError on any
failure (network down, bad credentials, server error) rather than a
low-level urllib exception, so storage.py can catch one exception type
and fall back to local-only behavior without the desktop app ever being
disrupted by a flaky or unconfigured connection.
"""
import json
import urllib.error
import urllib.request

TIMEOUT = 5


class SyncError(Exception):
    """Every failure talking to Supabase. `status` is the HTTP status (None
    for a network-level failure) and `code` PostgREST's error code, when
    there was one. `transient` says whether retrying later could help
    (offline, server hiccup, expired session, cloud schema not updated yet)
    as opposed to the request itself being wrong (e.g. "task not found"),
    which the sync queue drops rather than retrying forever."""

    def __init__(self, message, status=None, code=None, auth=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.auth = auth  # the login session itself is dead; needs a fresh log-in

    @property
    def schema_outdated(self):
        # PostgREST answers 404 / PGRST202 for a function that doesn't exist
        # yet, i.e. schema.sql hasn't been re-run since an app update.
        return self.status == 404 or self.code == "PGRST202"

    @property
    def transient(self):
        if self.auth or self.status is None or self.schema_outdated:
            return True
        return self.status >= 500 or self.status in (401, 408, 429)


class SupabaseSync:
    def __init__(self, url, anon_key, access_token=None, refresh_token=None):
        self.url = (url or "").rstrip("/")
        self.anon_key = anon_key or ""
        self.access_token = access_token
        self.refresh_token = refresh_token

    @property
    def configured(self):
        return bool(self.url and self.anon_key)

    @property
    def logged_in(self):
        return bool(self.access_token)

    def _request(self, path, body=None, method="POST", auth=True):
        headers = {
            "Content-Type": "application/json",
            "apikey": self.anon_key,
        }
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        try:
            req = urllib.request.Request(f"{self.url}{path}", data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            code = None
            try:
                code = json.loads(detail).get("code")
            except (ValueError, AttributeError):
                pass
            raise SyncError(f"{e.code}: {detail}", status=e.code, code=code) from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            # ValueError covers a malformed self.url (e.g. missing scheme,
            # a typo like "https:/") from urllib.request.Request/urlopen —
            # user-entered-URL mistakes must surface as a friendly error in
            # the Cloud Sync dialog, never as an uncaught crash.
            raise SyncError(str(e)) from e

    # ---------- Auth ----------

    def login(self, email, password):
        result = self._request(
            "/auth/v1/token?grant_type=password",
            {"email": email, "password": password},
            auth=False,
        )
        self.access_token = result["access_token"]
        self.refresh_token = result["refresh_token"]
        return result

    def sign_up(self, email, password):
        result = self._request(
            "/auth/v1/signup",
            {"email": email, "password": password},
            auth=False,
        )
        if result and result.get("access_token"):
            self.access_token = result["access_token"]
            self.refresh_token = result["refresh_token"]
        return result

    def refresh_session(self):
        if not self.refresh_token:
            raise SyncError("no refresh token")
        result = self._request(
            "/auth/v1/token?grant_type=refresh_token",
            {"refresh_token": self.refresh_token},
            auth=False,
        )
        self.access_token = result["access_token"]
        self.refresh_token = result["refresh_token"]
        return result

    def logout(self):
        self.access_token = None
        self.refresh_token = None

    # ---------- RPC ----------

    def _rpc(self, name, params=None, retry_on_auth_error=True):
        try:
            return self._request(f"/rest/v1/rpc/{name}", params or {})
        except SyncError as e:
            if retry_on_auth_error and e.status == 401 and self.refresh_token:
                try:
                    self.refresh_session()
                except SyncError as refresh_error:
                    # A refused refresh (as opposed to a network blip) means
                    # the session is really gone: flag it so the sync queue
                    # holds its changes and waits for a new log-in instead
                    # of treating this as a bad request.
                    if refresh_error.status in (400, 401, 403):
                        refresh_error.auth = True
                    raise refresh_error from e
                return self._request(f"/rest/v1/rpc/{name}", params or {})
            raise

    def sync_pull(self):
        return self._rpc("sync_pull")

    def set_notes(self, text):
        return self._rpc("set_notes", {"p_notes": text})

    def set_tz_offset(self, minutes):
        return self._rpc("set_tz_offset", {"p_minutes": minutes})

    def add_task(self, text, recurrence, reminder_time=None, source="checklist", task_id=None):
        # task_id lets the desktop create the task under the id it already
        # gave it locally (safe to retry: the server returns the existing
        # row). Left out when None so an older cloud schema, which doesn't
        # know p_id yet, still accepts the call.
        params = {"p_text": text, "p_recurrence": recurrence, "p_reminder_time": reminder_time, "p_source": source}
        if task_id:
            params["p_id"] = task_id
        return self._rpc("add_task", params)

    def remove_task(self, task_id):
        return self._rpc("remove_task", {"p_task_id": task_id})

    def rename_task(self, task_id, text):
        return self._rpc("rename_task", {"p_task_id": task_id, "p_text": text})

    def set_task_reminder(self, task_id, reminder_time):
        return self._rpc("set_task_reminder", {"p_task_id": task_id, "p_reminder_time": reminder_time})

    def reorder_tasks(self, ordered_ids):
        return self._rpc("reorder_tasks", {"p_ordered_ids": ordered_ids})

    def complete_task(self, task_id, done):
        return self._rpc("complete_task", {"p_task_id": task_id, "p_done": done})

    def record_pomodoro_completed(self, minutes=25):
        return self._rpc("record_pomodoro_completed", {"p_minutes": minutes})

    def record_break_completed(self):
        return self._rpc("record_break_completed")

    def import_state(self, notes, xp, total_pomodoros, total_tasks, current_streak, longest_streak, last_active_date):
        return self._rpc("import_state", {
            "p_notes": notes,
            "p_xp": xp,
            "p_total_pomodoros": total_pomodoros,
            "p_total_tasks": total_tasks,
            "p_current_streak": current_streak,
            "p_longest_streak": longest_streak,
            "p_last_active_date": last_active_date,
        })

    # ---------- Household (shared list) ----------

    def household_pull(self):
        return self._rpc("household_pull")

    def household_create(self, name):
        return self._rpc("household_create", {"p_name": name})

    def household_join(self, code, name):
        return self._rpc("household_join", {"p_code": code, "p_name": name})

    def household_leave(self):
        return self._rpc("household_leave")

    @staticmethod
    def _schedule_params(schedule):
        return {
            "p_recurrence": schedule["recurrence"], "p_weekday": schedule["weekday"],
            "p_month_day": schedule["month_day"], "p_due_date": schedule["due_date"],
            "p_due_time": schedule["due_time"],
        }

    def shared_add_task(self, text, task_id=None, schedule=None):
        params = {"p_text": text}
        if task_id:
            params["p_id"] = task_id
        if schedule:  # only sent when there is one, so plain items still work on an older schema
            params.update(self._schedule_params(schedule))
        return self._rpc("shared_add_task", params)

    def shared_set_schedule(self, task_id, schedule):
        return self._rpc("shared_set_schedule", {"p_id": task_id, **self._schedule_params(schedule)})

    def shared_reorder(self, ordered_ids):
        return self._rpc("shared_reorder", {"p_ordered_ids": ordered_ids})

    def shared_set_done(self, task_id, done):
        return self._rpc("shared_set_done", {"p_id": task_id, "p_done": done})

    def shared_remove_task(self, task_id):
        return self._rpc("shared_remove_task", {"p_id": task_id})

    def shared_clear_done(self):
        return self._rpc("shared_clear_done")
