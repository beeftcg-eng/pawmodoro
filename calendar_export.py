"""
calendar_export.py - Pure logic (no Qt) for View -> "Export to calendar
(.ics)…": an iCalendar file of everything with a date or a schedule, to
import into Google Calendar, Outlook, Apple Calendar, Thunderbird...

    personal checklist   a one-off with a due date (all day), a monthly task
                         on its day, a specific-day task every week, and a
                         daily task with a reminder time (at that time)
    shared list          a one-off with a date, and daily / weekly / monthly
                         items (at their time, if they have one)
    calendar events      yours and the household's, over their days (all
                         day, or from their start time to their end time)

Plain daily / weekly tasks without a time would only fill every day of the
calendar, so they're left out. Times are "floating" (no time zone): the
calendar shows them at that time wherever it is. It's a snapshot: re-export
after changing things (importing again updates the same events, by UID).
"""
from datetime import date, datetime, timedelta, timezone

import shared_schedule
import task_dates

BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
TIMED_EVENT_MINUTES = 15


def _escape(text):
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line):
    """RFC 5545 lines are at most 75 octets; longer ones continue on the
    next line after a space. Never splits a UTF-8 character."""
    out, current = [], ""
    for ch in line:
        limit = 75 if not out else 74  # continuation lines start with a space
        if len((current + ch).encode("utf-8")) > limit:
            out.append(current)
            current = ch
        else:
            current += ch
    out.append(current)
    return "\r\n ".join(out)


def _monthly_rule(month_day):
    """Monthly on `month_day`, or a shorter month's last day -- like the app.
    (A plain BYMONTHDAY=31 would skip every 30-day month.)"""
    if month_day <= 28:
        return f"FREQ=MONTHLY;BYMONTHDAY={month_day}"
    days = ",".join(str(d) for d in range(28, month_day + 1))
    return f"FREQ=MONTHLY;BYMONTHDAY={days};BYSETPOS=-1"


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _first_on_or_after(today, weekday):
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def _event(uid, summary, start, rule=None, at=None, description=None):
    """One VEVENT: all day on `start`, or at "HH:MM" `at` for 15 minutes."""
    lines = ["BEGIN:VEVENT", f"UID:{uid}@pawmodoro"]
    if at:
        begins = datetime.combine(start, datetime.strptime(at, "%H:%M").time())
        ends = begins + timedelta(minutes=TIMED_EVENT_MINUTES)
        lines += [f"DTSTART:{begins:%Y%m%dT%H%M%S}", f"DTEND:{ends:%Y%m%dT%H%M%S}"]
    else:
        lines += [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{start + timedelta(days=1):%Y%m%d}"]
    if rule:
        lines.append(f"RRULE:{rule}")
    lines.append(f"SUMMARY:{_escape(summary)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    lines.append("END:VEVENT")
    return lines


def personal_events(tasks, today):
    events = []
    for task in tasks:
        if task.get("source") == "wishlist":
            continue
        uid, text = f"task-{task['id']}", task.get("text", "")
        note = (task.get("note") or "").strip() or None
        recurrence, at = task.get("recurrence"), task.get("reminder_time")
        if recurrence == "once":
            due = _parse_date(task.get("due_date"))
            if due and not task.get("completed_today"):
                events.append(_event(uid, text, due, at=at, description=note))
        elif recurrence == "monthly":
            month_day = task.get("month_day") or 1
            start = task_dates.month_occurrence(month_day, today)
            events.append(_event(uid, text, start, _monthly_rule(month_day), at, note))
        elif recurrence == "weekday" and task.get("weekday") is not None:
            start = _first_on_or_after(today, task["weekday"])
            events.append(_event(uid, text, start, f"FREQ=WEEKLY;BYDAY={BYDAY[task['weekday']]}", at, note))
        elif recurrence == "daily" and at:
            events.append(_event(uid, text, today, "FREQ=DAILY", at, note))
    return events


def shared_events(household, today):
    events = []
    for task in (household or {}).get("tasks", []):
        schedule = shared_schedule.normalize(task)
        uid, text, at = f"shared-{task['id']}", task.get("text", ""), schedule["due_time"]
        recurrence = schedule["recurrence"]
        if recurrence == "once":
            due = _parse_date(schedule["due_date"])
            if due and not task.get("done"):
                events.append(_event(uid, text, due, at=at))
        elif recurrence == "daily":
            if at:
                events.append(_event(uid, text, today, "FREQ=DAILY", at))
        elif recurrence == "weekly":
            start = _first_on_or_after(today, schedule["weekday"])
            events.append(_event(uid, text, start, f"FREQ=WEEKLY;BYDAY={BYDAY[schedule['weekday']]}", at))
        elif recurrence == "monthly":
            start = shared_schedule.last_occurrence(schedule, today)
            events.append(_event(uid, text, start, _monthly_rule(schedule["month_day"]), at))
    return events


EVENT_DEFAULT_MINUTES = 60  # a calendar event with a start time but no end time


def calendar_events(events, household=None):
    out = []
    for event, shared in [(e, False) for e in events or []] + [(e, True) for e in (household or {}).get("events", []) or []]:
        start, end = _parse_date(event.get("start_date")), _parse_date(event.get("end_date"))
        if start is None:
            continue
        end = end or start
        lines = ["BEGIN:VEVENT", f"UID:event-{event['id']}@pawmodoro"]
        if event.get("start_time"):
            begins = datetime.combine(start, datetime.strptime(event["start_time"], "%H:%M").time())
            if event.get("end_time"):
                ends = datetime.combine(end, datetime.strptime(event["end_time"], "%H:%M").time())
            elif end == start:
                ends = begins + timedelta(minutes=EVENT_DEFAULT_MINUTES)
            else:
                ends = datetime.combine(end + timedelta(days=1), datetime.min.time())
            lines += [f"DTSTART:{begins:%Y%m%dT%H%M%S}", f"DTEND:{max(ends, begins):%Y%m%dT%H%M%S}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end + timedelta(days=1):%Y%m%d}"]
        lines.append(f"SUMMARY:{_escape(event.get('title', ''))}")
        note = (event.get("note") or "").strip()
        if note:
            lines.append(f"DESCRIPTION:{_escape(note)}")
        if shared:
            lines.append("CATEGORIES:Shared")
        lines.append("END:VEVENT")
        out.append(lines)
    return out


def build_ics(tasks, household=None, today=None, now=None, events=None):
    """The whole .ics text, and how many events it holds."""
    today = today or date.today()
    stamp = f"DTSTAMP:{(now or datetime.now(timezone.utc)):%Y%m%dT%H%M%SZ}"
    events = calendar_events(events, household) + personal_events(tasks, today) + shared_events(household, today)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Pawmodoro//Checklist//EN", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:Pawmodoro"]
    for event in events:
        lines += event[:2] + [stamp] + event[2:]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n", len(events)
