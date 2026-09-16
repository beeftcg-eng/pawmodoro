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
    pass


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
            raise SyncError(f"{e.code}: {detail}") from e
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
            if retry_on_auth_error and "401" in str(e) and self.refresh_token:
                self.refresh_session()
                return self._request(f"/rest/v1/rpc/{name}", params or {})
            raise

    def sync_pull(self):
        return self._rpc("sync_pull")

    def set_notes(self, text):
        return self._rpc("set_notes", {"p_notes": text})

    def add_task(self, text, recurrence, reminder_time=None):
        return self._rpc("add_task", {"p_text": text, "p_recurrence": recurrence, "p_reminder_time": reminder_time})

    def remove_task(self, task_id):
        return self._rpc("remove_task", {"p_task_id": task_id})

    def set_task_reminder(self, task_id, reminder_time):
        return self._rpc("set_task_reminder", {"p_task_id": task_id, "p_reminder_time": reminder_time})

    def complete_task(self, task_id, done):
        return self._rpc("complete_task", {"p_task_id": task_id, "p_done": done})

    def set_task_completed_flag(self, task_id, done):
        return self._rpc("set_task_completed_flag", {"p_task_id": task_id, "p_done": done})

    def apply_task_xp(self, recurrence, done):
        return self._rpc("apply_task_xp", {"p_recurrence": recurrence, "p_done": done})

    def record_pomodoro_completed(self):
        return self._rpc("record_pomodoro_completed")

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
