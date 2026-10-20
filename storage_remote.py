"""
storage_remote.py - The half of Storage that applies a pull: the cloud's
state replacing the local cache (the server is authoritative), carrying
across what only this computer knows (reminder clocks, pages and notes
being typed in). Mixed into storage.Storage; see
CLAUDE.md's "Cloud sync" for the rules.
"""
from datetime import date, datetime

import gamification


class RemoteStateMixin:
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
        if "timer" in remote:  # v2.20; followed by PomodoroTab.follow_remote_timer
            self.data["remote_timer"] = remote["timer"]
        local_tasks = {t["id"]: t for t in self.data.get("checklist", [])}
        self.data["checklist"] = [
            {
                "id": t["id"],
                "text": t["text"],
                "recurrence": t["recurrence"],
                "weekday": t.get("weekday"),
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
        ]
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
        self._apply_remote_calendar(remote)

    def _apply_remote_calendar(self, remote):
        """Events and files from a pull. One made here that the cloud doesn't
        have yet (made while sync was off, or while the cloud was older) is
        kept and sent again. Absent from an older cloud: nothing changes."""
        if "events" in remote:
            remote_ids = {e["id"] for e in remote["events"]}
            leftover = [e for e in self.data.get("events", []) if not e.get("synced") and e["id"] not in remote_ids]
            self.data["events"] = [dict(self._event_fields(e), synced=True) for e in remote["events"]] + leftover
            for event in leftover:
                if not event.get("sync_error"):
                    self._enqueue_event(event, False)
        if "attachments" in remote:
            remote_ids = {a["id"] for a in remote["attachments"]}
            leftover = [a for a in self.data.get("attachments", [])
                        if not a.get("uploaded") and a["id"] not in remote_ids and self._item_exists(a)]
            self.data["attachments"] = [dict(a, uploaded=True) for a in remote["attachments"]] + leftover
            for attachment in leftover:
                if not attachment.get("upload_error"):
                    self._enqueue_attachment(attachment)
        if "attachments_trash" in remote:
            self.data["attachment_trash"] = list(remote["attachments_trash"] or [])

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
        """Monthly day, due date, note, subtasks and done days from a pull. A
        cloud older than v2.18 (v2.20 for done days) has none of them; then
        this computer's own are kept."""
        # done_days (v2.20) on its own: a v2.18 cloud has subtasks but not it.
        done_days = remote_task.get("done_days")
        done_days = [str(d) for d in done_days] if isinstance(done_days, list) else local_task.get("done_days", [])
        if "subtasks" not in remote_task:
            return {"done_days": done_days, **{k: local_task.get(k, default) for k, default in
                    (("month_day", None), ("due_date", None), ("note", ""), ("subtasks", []))}}
        subtasks = remote_task.get("subtasks")
        return {
            "done_days": done_days,
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
        sync_engine.py); `remote` None = unchanged since the last pull
        applied. `household`: False = leave the cached household
        alone, None = the account isn't in one, else the household dict.
        `keep_notes`: leave the notes text alone (the editor is being used).
        When `rev` is given (the value when the pull began), nothing is
        applied -- and False is returned -- if anything changed locally since
        or changes are still waiting to upload, so a stale pull can never
        overwrite a fresh edit. Returns True when applied."""
        if rev is not None and (rev != self.rev or self.outbox_len()):
            return False
        state_fp, household_fp, _ = self.sync.applied
        if remote is not None:  # None: unchanged since the last one applied
            state_fp = remote.pop("fingerprint", None)
            self._apply_remote_state(remote, keep_notes=keep_notes)
        if household is not False:
            household_fp = household.pop("fingerprint", None) if household else None
            self.data["household"] = household
        self._prune_attachment_cache()
        self.save()
        # Lets the next poll ask "anything new since this?" -- unless the
        # notes were held back for the editor, which then has to see the
        # next full pull.
        self.sync.applied = (None if keep_notes else state_fp, household_fp, rev)
        return True
