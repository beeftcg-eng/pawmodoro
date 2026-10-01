"""
Shared test helpers. Every test gets a Storage backed by its own temporary
folder, so nothing ever touches the real ~/.local/share/pawmodoro.
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# A test must never pop up a window or a real network check.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PAWMODORO_DISABLE_UPDATE_CHECK"] = "1"

import storage  # noqa: E402


class StorageTestCase(unittest.TestCase):
    """self.storage is a fresh Storage in a temp dir; self.new_storage()
    re-opens the same files (as a restart would)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pawmodoro-test-")
        self._saved = (storage.APP_DIR, storage.DATA_FILE, storage.BACKUP_DIR)
        storage.APP_DIR = self.tmp
        storage.DATA_FILE = os.path.join(self.tmp, "data.json")
        storage.BACKUP_DIR = os.path.join(self.tmp, "backups")
        self.storage = self.new_storage()

    def tearDown(self):
        storage.APP_DIR, storage.DATA_FILE, storage.BACKUP_DIR = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_storage(self):
        return storage.Storage()

    def enable_sync(self, st=None):
        """Makes the outbox record changes (no network: the engine isn't started)."""
        st = st or self.storage
        st.data["sync"].update(enabled=True, url="https://example.invalid", anon_key="k", refresh_token="t")

    def outbox_ops(self, st=None):
        return [o["op"] for o in (st or self.storage).data["sync_outbox"]]


def remote_state(checklist, notes=""):
    """The shape sync_pull returns (see supabase/schema.sql)."""
    return {"notes": notes, "checklist": checklist, "xp": 0, "total_pomodoros": 0, "total_tasks": 0,
            "current_streak": 0, "longest_streak": 0, "quests": [], "weekly_quests": [], "history": []}
