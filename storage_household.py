"""
storage_household.py - The shared (household) list's half of Storage:
the cached household, edits queued for upload, its schedules rolling over
and its reminders. Mixed into storage.Storage.
"""
import uuid
from datetime import date, datetime

import shared_schedule


class HouseholdMixin:
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
            args["schedule"] = schedule  # plain items take the cloud's defaults
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

    def shared_assign(self, task_id, user_id):
        """Gives a shared item to one household member (None = anyone); its
        reminders then go only to them."""
        household = self.get_household()
        members = {m.get("user_id"): m for m in (household or {}).get("members", [])}
        for task in (household or {}).get("tasks", []):
            if task["id"] == task_id:
                member = members.get(user_id) if user_id else None
                task["assigned_to"] = user_id if member else None
                task["assigned_name"] = member.get("name") if member else None
                task["assigned_to_me"] = bool(member and member.get("is_me"))
        self._enqueue("shared_assign", {"id": task_id, "user_id": user_id if user_id in members else None})
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
            if task.get("done") or (task.get("assigned_to") and not task.get("assigned_to_me")):
                continue  # someone else's turn: their reminder, not yours
            occurrence = shared_schedule.reminder_occurrence(self._shared_schedule_of(task), now)
            if occurrence and reminded.get(task["id"]) != occurrence:
                reminded[task["id"]] = occurrence
                due.append(task)
        if due or stale:
            self.save()
        return due
