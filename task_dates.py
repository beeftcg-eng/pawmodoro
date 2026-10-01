"""
task_dates.py - Pure logic (no Qt, no storage) for the dates on personal
checklist tasks: monthly tasks ("pay rent on the 1st") and due dates on
one-off tasks.

    recurrence  "daily" | "weekly" | "once" | "weekday" | "monthly"
    month_day   1..31 for "monthly" (months that are too short use their
                last day), else None
    due_date    "YYYY-MM-DD" for a "once" task, or None

Mirrored in supabase/schema.sql (personal_month_occurrence,
roll_recurring_tasks, complete_task, checklist_done_on, due_push_reminders)
-- change both, or desktop-offline and cloud diverge.
"""
from datetime import date

import shared_schedule

MONTH_DAYS = range(1, 32)


def normalize(recurrence, month_day=None, due_date=None):
    """(month_day, due_date) cleaned for `recurrence`: a monthly task always
    has a day of the month, only a one-off can have a due date."""
    if recurrence == "monthly":
        month_day = month_day if month_day in MONTH_DAYS else 1
    else:
        month_day = None
    if recurrence != "once" or not _parse(due_date):
        due_date = None
    return month_day, due_date


def _parse(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def month_occurrence(month_day, today):
    """The most recent day on or before `today` that a monthly task came due."""
    return shared_schedule.last_occurrence({"recurrence": "monthly", "month_day": month_day}, today)


def monthly_needs_reset(last_completed, month_day, today):
    """A monthly task un-ticks itself once its day comes round again."""
    return not last_completed or last_completed < month_occurrence(month_day or 1, today).isoformat()


def reminds_today(task, today):
    """Whether a task's reminders may fire on `today`: a specific-day task
    only on its weekday, a monthly one only on its day of the month, and a
    one-off with a due date not before that date."""
    recurrence = task.get("recurrence")
    if recurrence == "weekday":
        return task.get("weekday") in (None, today.weekday())
    if recurrence == "monthly":
        return month_occurrence(task.get("month_day") or 1, today) == today
    due = _parse(task.get("due_date"))
    if recurrence == "once" and due is not None:
        return due <= today
    return True


def due_status(task, today):
    """"overdue", "today" or None, for a one-off with a due date that
    isn't done yet."""
    due = _parse(task.get("due_date"))
    if task.get("recurrence") != "once" or due is None or task.get("completed_today"):
        return None
    if due < today:
        return "overdue"
    return "today" if due == today else None


def _ordinal(n):
    return shared_schedule._ordinal(n)


def describe(task, today=None):
    """The schedule part of a task's label: "monthly, 15th", "due Fri 3 Oct"."""
    recurrence = task.get("recurrence")
    if recurrence == "monthly":
        return f"monthly, {_ordinal(task.get('month_day') or 1)}"
    due = _parse(task.get("due_date"))
    if recurrence == "once" and due is not None:
        today = today or date.today()
        if due == today:
            return "due today"
        fmt = "%a %d %b" if due.year == today.year else "%a %d %b %Y"
        return f"due {due.strftime(fmt)}"
    return recurrence or ""
