"""
calendar_view.py - Pure logic (no Qt, no storage) for the Calendar tab: what
falls on a given day, from the personal checklist, the shared list and the
calendar's own events.

    personal task   a one-off on its due date, a monthly task on its day, a
                    specific-day task on its weekday (daily / weekly tasks
                    aren't tied to a day, so they stay off the calendar)
    shared item     a one-off on its date, weekly on its weekday, monthly
                    on its day (daily ones stay off, like personal ones)
    event           every day from its start date to its end date

An entry is a dict: kind ("event" | "task" | "shared"), id, title, time
("HH:MM" or None), done, shared (an event on the household's calendar) and
span (None, or "start" / "middle" / "end" for an event over several days).

task_on_day / task_done_on / shared_on_day / shared_done_on are mirrored by
taskOnDay / taskDoneOn / sharedOnDay / sharedDoneOn in docs/app.js (checked
by tests/parity) -- change both.
"""
import calendar
from datetime import date, timedelta

import shared_schedule
import task_dates

KIND_ORDER = {"event": 0, "shared": 1, "task": 2}


def _parse(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def task_on_day(task, day):
    if task.get("source") == "wishlist":
        return False
    recurrence = task.get("recurrence")
    if recurrence == "once":
        return _parse(task.get("due_date")) == day
    if recurrence == "monthly":
        return task_dates.month_occurrence(task.get("month_day") or 1, day) == day
    if recurrence == "weekday":
        return task.get("weekday") == day.weekday()
    return False


def task_done_on(task, day):
    """Ticked for that day: in its done days, or a one-off that's ticked."""
    if day.isoformat() in (task.get("done_days") or []):
        return True
    return task.get("recurrence") == "once" and bool(task.get("completed_today"))


def shared_on_day(task, day):
    schedule = shared_schedule.normalize({k: task.get(k) for k in shared_schedule.DEFAULT_SCHEDULE})
    recurrence = schedule["recurrence"]
    if recurrence == "once":
        return _parse(schedule["due_date"]) == day
    if recurrence in ("weekly", "monthly"):
        return shared_schedule.last_occurrence(schedule, day) == day
    return False


def shared_done_on(task, day, today):
    """A shared item only knows whether its current occurrence is ticked."""
    if not task.get("done"):
        return False
    if task.get("recurrence", "once") == "once":
        return True
    schedule = shared_schedule.normalize({k: task.get(k) for k in shared_schedule.DEFAULT_SCHEDULE})
    return shared_schedule.last_occurrence(schedule, today) == day


def event_on_day(event, day):
    start, end = _parse(event.get("start_date")), _parse(event.get("end_date"))
    return start is not None and start <= day <= (end or start)


def _event_entry(event, day, shared):
    start, end = _parse(event["start_date"]), _parse(event.get("end_date")) or _parse(event["start_date"])
    if start == end:
        span = None
    else:
        span = "start" if day == start else "end" if day == end else "middle"
    return {"kind": "event", "id": event["id"], "title": event.get("title", ""),
            "time": event.get("start_time") if day == start else None,
            "done": False, "shared": shared, "span": span}


def entries_on(day, tasks, household=None, events=None, today=None):
    """Everything on `day`, sorted: all-day things first, then by time."""
    today = today or date.today()
    out = []
    for event in events or []:
        if event_on_day(event, day):
            out.append(_event_entry(event, day, False))
    for event in (household or {}).get("events", []) or []:
        if event_on_day(event, day):
            out.append(_event_entry(event, day, True))
    for task in (household or {}).get("tasks", []) or []:
        if shared_on_day(task, day):
            out.append({"kind": "shared", "id": task["id"], "title": task.get("text", ""),
                        "time": task.get("due_time"), "done": shared_done_on(task, day, today),
                        "shared": True, "span": None})
    for task in tasks or []:
        if task_on_day(task, day):
            out.append({"kind": "task", "id": task["id"], "title": task.get("text", ""),
                        "time": task.get("reminder_time"), "done": task_done_on(task, day),
                        "shared": False, "span": None})
    out.sort(key=lambda e: (e["time"] is not None, e["time"] or "", KIND_ORDER[e["kind"]], e["title"].lower()))
    return out


def month_entries(year, month, tasks, household=None, events=None, today=None):
    """{date: entries} for every day of a month that has something on it."""
    days = calendar.monthrange(year, month)[1]
    result = {}
    for n in range(1, days + 1):
        day = date(year, month, n)
        entries = entries_on(day, tasks, household, events, today)
        if entries:
            result[day] = entries
    return result


def describe_event(event, today=None):
    """"Tue 20 Oct, 08:30", "Tue 20 Oct – Sun 25 Oct", "Tue 20 Oct, 19:00–21:00"."""
    today = today or date.today()
    start = _parse(event.get("start_date"))
    end = _parse(event.get("end_date")) or start
    if start is None:
        return ""

    def fmt(d):
        return d.strftime("%a %d %b" if d.year == today.year else "%a %d %b %Y")

    start_time, end_time = event.get("start_time"), event.get("end_time")
    text = fmt(start) + (f", {start_time}" if start_time else "")
    if end != start:
        text += f" – {fmt(end)}" + (f", {end_time}" if end_time else "")
    elif end_time:
        text += f"–{end_time}"
    return text


def item_label(kind):
    return {"event": "Event", "task": "Task", "shared": "Shared item"}[kind]


def human_size(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def week_rows(year, month, first_weekday=0):
    """The weeks a month grid shows: lists of 7 dates (spilling into the
    months either side)."""
    first = date(year, month, 1)
    start = first - timedelta(days=(first.weekday() - first_weekday) % 7)
    last = date(year, month, calendar.monthrange(year, month)[1])
    rows = []
    day = start
    while day <= last:
        rows.append([day + timedelta(days=n) for n in range(7)])
        day += timedelta(days=7)
    return rows
