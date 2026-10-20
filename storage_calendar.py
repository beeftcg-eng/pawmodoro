"""
storage_calendar.py - Calendar events and attached files, Storage's half:
personal ones in data["events"] / data["attachments"], the household's in
data["household"]. File bytes are cached under storage.attachments_dir().
Mixed into storage.Storage.
"""
import mimetypes
import os
import shutil
import uuid
from datetime import date, datetime

import storage as _storage  # the module: its paths are what tests repoint


class CalendarMixin:
    # ---------- Calendar: events and attached files (v2.21) ----------
    @staticmethod
    def _event_fields(event):
        return {k: event.get(k) for k in ("id", "title", "start_date", "end_date", "start_time", "end_time")} | {
            "note": event.get("note") or "", "created_by_me": event.get("created_by_me", True),
            "created_by_name": event.get("created_by_name")}

    def get_events(self):
        return self.data.get("events", [])

    def get_shared_events(self):
        return (self.get_household() or {}).get("events", []) or []

    def find_event(self, event_id):
        """(event, shared) -- (None, False) when there's no such event."""
        for event in self.get_events():
            if event["id"] == event_id:
                return event, False
        for event in self.get_shared_events():
            if event["id"] == event_id:
                return event, True
        return None, False

    def save_event(self, title, start_date, end_date=None, start_time=None, end_time=None, note="", shared=False,
                   event_id=None):
        """Adds an event, or changes the one with `event_id`. Dates are
        "YYYY-MM-DD", times "HH:MM" (no start time = all day). `shared` puts it
        on the household's calendar. Raises ValueError for one that ends
        before it starts."""
        title = (title or "").strip()[:200]
        end_date = end_date or start_date
        if not title:
            raise ValueError("An event needs a title.")
        if end_date < start_date or (end_date == start_date and start_time and end_time and end_time < start_time):
            raise ValueError("The event ends before it starts.")
        if (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days > 366:
            raise ValueError("An event can last at most a year.")
        household = self.get_household()
        shared = bool(shared and household)
        existing, was_shared = self.find_event(event_id) if event_id else (None, False)
        if was_shared and not shared:  # the same rules as save_event in schema.sql
            if not existing.get("created_by_me", True):
                raise ValueError("Only whoever added it can take it off the household's calendar.")
            if any(not a.get("added_by_me", True) for a in self.attachments_for("event", event_id)):
                raise ValueError("Others attached files to it, so it stays on the household's calendar.")
        event = {**(existing or {}), "id": event_id or uuid.uuid4().hex[:12], "title": title,
                 "start_date": start_date, "end_date": end_date, "start_time": start_time or None,
                 "end_time": (end_time or None) if start_time else None, "note": note or ""}
        if existing is None:
            event.update(created_by_me=True, created_by_name=self.household_my_name() if shared else None)
        old_list = (household or {}).get("events", []) if was_shared else self.data["events"]
        new_list = household.setdefault("events", []) if shared else self.data["events"]
        index = next((i for i, e in enumerate(old_list) if e["id"] == event["id"]), None)
        if index is not None:
            del old_list[index]
        event.pop("sync_error", None)
        if not shared:
            event["synced"] = False
        if index is not None and old_list is new_list:
            new_list.insert(index, event)
        else:
            new_list.append(event)
        if existing is not None and was_shared != shared:
            self._move_event_attachments(event["id"], shared)
        self._enqueue_event(event, shared)
        self.save()
        return event

    def _enqueue_event(self, event, shared):
        self._enqueue("event_save", {**{k: event.get(k) for k in (
            "id", "title", "start_date", "end_date", "start_time", "end_time", "note")}, "shared": shared},
            key=f"event:{event['id']}")

    def remove_event(self, event_id):
        event, shared = self.find_event(event_id)
        if event is None:
            return
        events = self.get_shared_events() if shared else self.data["events"]
        events[:] = [e for e in events if e["id"] != event_id]
        # The cloud puts its uploaded files in the trash; drop them here.
        for attachment in self.attachments_for("event", event_id):
            self._drop_attachment(attachment, cloud=False)
        self._enqueue("event_remove", {"id": event_id}, key=f"event:{event_id}")
        self.save()

    def _move_event_attachments(self, event_id, shared):
        household = self.get_household() or {}
        source = self.data["attachments"] if shared else household.setdefault("attachments", [])
        target = household.setdefault("attachments", []) if shared else self.data["attachments"]
        moving = [a for a in source if a["item_kind"] == "event" and a["item_id"] == event_id]
        source[:] = [a for a in source if a not in moving]
        target.extend(moving)

    # Files. A personal task's or event's go in data["attachments"], a shared
    # item's or household event's in the household's. Each is copied into
    # _storage.attachments_dir() and uploaded by the sync engine (attachment_add); a
    # file from elsewhere is downloaded the first time it's opened.
    def _attachment_lists(self):
        return [self.data.get("attachments", []), (self.get_household() or {}).get("attachments", []) or []]

    def attachments_for(self, kind, item_id):
        return [a for lst in self._attachment_lists() for a in lst
                if a.get("item_kind") == kind and a.get("item_id") == item_id]

    def find_attachment(self, attachment_id):
        return next((a for lst in self._attachment_lists() for a in lst if a["id"] == attachment_id), None)

    def _item_exists(self, attachment):
        kind, item_id = attachment.get("item_kind"), attachment.get("item_id")
        if kind == "task":
            return self._find_task(item_id) is not None
        if kind == "event":
            return self.find_event(item_id)[0] is not None
        return self._find_shared(item_id) is not None

    def add_attachment(self, kind, item_id, source_path):
        """Attaches a copy of the file at `source_path` to a task ("task"),
        shared item ("shared") or event ("event"). Raises ValueError when
        it's too big or the item can't take files."""
        size = os.path.getsize(source_path)
        if size > _storage.ATTACHMENT_MAX_BYTES:
            raise ValueError(f"Files can be at most {_storage.ATTACHMENT_MAX_BYTES // (1024 * 1024)} MB.")
        household = self.get_household()
        shared = kind == "shared" or (kind == "event" and self.find_event(item_id)[1])
        if shared and not household:
            raise ValueError("Join a household first (Shared tab).")
        name = os.path.basename(source_path)
        entry = {
            "id": uuid.uuid4().hex[:12], "item_kind": kind, "item_id": item_id,
            "file_name": name[:200], "mime": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "size": size, "path": None, "uploaded": False, "upload_error": None,
            "added_by_me": True, "added_by_name": self.household_my_name() if shared else None,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        target = _storage.attachment_file(entry["id"], entry["file_name"])
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source_path, target)
        (household.setdefault("attachments", []) if shared else self.data["attachments"]).append(entry)
        self._enqueue_attachment(entry)
        self.save()
        return entry

    def _enqueue_attachment(self, entry):
        household = self.get_household() or {}
        shared = entry["item_kind"] == "shared" or entry in (household.get("attachments") or [])
        self._enqueue("attachment_add", {
            "id": entry["id"], "item_kind": entry["item_kind"], "item_id": entry["item_id"],
            "file_name": entry["file_name"], "mime": entry["mime"], "size": entry["size"],
            "household_id": household.get("id") if shared else None,
        }, key=f"attach:{entry['id']}")

    def remove_attachment(self, attachment_id):
        attachment = self.find_attachment(attachment_id)
        if attachment is not None:
            self._drop_attachment(attachment, cloud=True)
            self.save()

    def _drop_attachment(self, attachment, cloud):
        """Forgets a file here; with `cloud`, deletes it there too (an upload
        still waiting is simply cancelled: same outbox key)."""
        for lst in self._attachment_lists():
            lst[:] = [a for a in lst if a["id"] != attachment["id"]]
        shutil.rmtree(os.path.join(_storage.attachments_dir(), _storage.safe_file_name(attachment["id"])), ignore_errors=True)
        if cloud:
            household = self.get_household() or {}
            self._enqueue("attachment_remove", {
                "id": attachment["id"], "path": attachment.get("path"),
                "household_id": household.get("id") if attachment.get("item_kind") == "shared" else None,
            }, key=f"attach:{attachment['id']}")
        else:
            # Not the cloud's business, but a waiting upload has to go.
            with self._lock:
                outbox = self.data.get("sync_outbox", [])
                outbox[:] = [o for o in outbox
                             if o.get("key") != f"attach:{attachment['id']}" or o["seq"] == self._outbox_inflight]

    def attachment_local_path(self, attachment):
        """The file on this computer, or None if it hasn't been downloaded."""
        path = _storage.attachment_file(attachment["id"], attachment["file_name"])
        return path if os.path.exists(path) else None

    def purge_trashed_attachments(self, now=None):
        """Deletes, from the cloud, this account's files whose item was
        removed over an hour ago (sync_pull's attachments_trash)."""
        if not self.sync_configured():
            return
        now = now or datetime.now().astimezone()
        trash = self.data.get("attachment_trash", [])
        keep = []
        for entry in trash:
            try:
                trashed = datetime.fromisoformat(str(entry.get("trashed_at")).replace("Z", "+00:00"))
                if trashed.tzinfo is None:
                    trashed = trashed.astimezone()
            except ValueError:
                trashed = now
            if (now - trashed).total_seconds() >= _storage.ATTACHMENT_TRASH_SECONDS:
                self._enqueue("attachment_remove", {"id": entry["id"], "path": entry.get("path")},
                              key=f"attach:{entry['id']}")
            else:
                keep.append(entry)
        if len(keep) != len(trash):
            self.data["attachment_trash"] = keep
            self.save()

    def _prune_orphan_attachments(self):
        """At startup (long after any undo): files on personal tasks and
        events that are gone. The cloud trashes the ones it knows about; a
        "specific day" task never reached it, so its files are deleted here."""
        orphans = [a for a in self.data.get("attachments", []) if not self._item_exists(a)]
        for attachment in orphans:
            # deleting one the cloud already trashed again does no harm
            self._drop_attachment(attachment, cloud=bool(attachment.get("uploaded")))
        if orphans:
            self.save()

    def _prune_attachment_cache(self):
        """Deletes downloaded files nothing refers to any more."""
        folder = _storage.attachments_dir()
        if not os.path.isdir(folder):
            return
        known = {_storage.safe_file_name(a["id"]) for lst in self._attachment_lists() for a in lst}
        for name in os.listdir(folder):
            if name not in known:
                shutil.rmtree(os.path.join(folder, name), ignore_errors=True)

    # ---- called by the sync thread ----
    def outbox_attachment_uploaded(self, attachment_id, path):
        with self._lock:
            attachment = self.find_attachment(attachment_id)
            if attachment is not None:
                attachment.update(path=path, uploaded=True, upload_error=None)
                self.save()

    def outbox_event_failed(self, event_id, message):
        """The cloud refused an event: it stays here and isn't sent again
        (until it's edited)."""
        with self._lock:
            event = next((e for e in self.data.get("events", []) if e["id"] == event_id), None)
            if event is not None:
                event["sync_error"] = str(message)[:300]
                self.save()

    def outbox_attachment_failed(self, attachment_id, message):
        """The cloud refused a file (too big, out of space, its item gone):
        it stays here, marked, and isn't sent again."""
        with self._lock:
            attachment = self.find_attachment(attachment_id)
            if attachment is not None:
                attachment["upload_error"] = str(message)[:300]
                self.save()
