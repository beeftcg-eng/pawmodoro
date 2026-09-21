"""
shared_schedule.py - Pure logic (no Qt, no storage) for scheduled items on the
Shared tab: a one-off item with an exact date and time, or an item that
repeats daily, weekly on a chosen weekday ("every Tuesday") or monthly on a
chosen day of the month, optionally at a time of day.

A schedule is a dict:
    recurrence  "once" | "daily" | "weekly" | "monthly"
    weekday     0=Monday..6=Sunday      (weekly only, else None)
    month_day   1..31                   (monthly only, else None; months that
                                         are too short use their last day)
    due_date    "YYYY-MM-DD"            (once only, else None)
    due_time    "HH:MM" or None

The reset rule is mirrored in supabase/schema.sql (shared_last_occurrence /
roll_shared_tasks) -- change both, or desktop-offline and cloud diverge.
"""
import calendar
import re
from datetime import date, datetime, time, timedelta

RECURRENCES = ("once", "daily", "weekly", "monthly")
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DEFAULT_SCHEDULE = {"recurrence": "once", "weekday": None, "month_day": None, "due_date": None, "due_time": None}

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def normalize(schedule):
    """A clean copy with every key present and fields that don't apply to the
    chosen recurrence cleared (a weekly item has no month_day, etc.)."""
    s = dict(DEFAULT_SCHEDULE)
    s.update({k: v for k, v in (schedule or {}).items() if k in DEFAULT_SCHEDULE})
    if s["recurrence"] not in RECURRENCES:
        s["recurrence"] = "once"
    if s["recurrence"] != "weekly" or s["weekday"] not in range(7):
        s["weekday"] = 0 if s["recurrence"] == "weekly" else None
    if s["recurrence"] != "monthly" or s["month_day"] not in range(1, 32):
        s["month_day"] = 1 if s["recurrence"] == "monthly" else None
    if s["recurrence"] != "once":
        s["due_date"] = None
    if not (isinstance(s["due_time"], str) and _TIME_RE.match(s["due_time"])):
        s["due_time"] = None
    if s["recurrence"] == "once" and s["due_time"] and not s["due_date"]:
        s["due_date"] = date.today().isoformat()  # a time on its own means "today"
    return s


def is_plain(schedule):
    """True for an ordinary one-off item with no date or time."""
    return normalize(schedule) == DEFAULT_SCHEDULE


def _month_day_in(year, month, month_day):
    return date(year, month, min(month_day, calendar.monthrange(year, month)[1]))


def last_occurrence(schedule, today):
    """The most recent day (on or before `today`) this item came due, or None
    for a one-off. A recurring item that was ticked before this day is due
    again."""
    recurrence = schedule.get("recurrence")
    if recurrence == "daily":
        return today
    if recurrence == "weekly":
        weekday = schedule.get("weekday")
        if weekday is None:
            return None
        return today - timedelta(days=(today.weekday() - weekday) % 7)
    if recurrence == "monthly":
        month_day = schedule.get("month_day")
        if month_day is None:
            return None
        candidate = _month_day_in(today.year, today.month, month_day)
        if candidate <= today:
            return candidate
        previous_last = today.replace(day=1) - timedelta(days=1)
        return _month_day_in(previous_last.year, previous_last.month, month_day)
    return None


def needs_reset(schedule, done_date, today):
    """True when a ticked item should un-tick itself: it repeats, and it has
    come due again since it was ticked. `done_date` is the local date it was
    ticked (None = unknown, so reset)."""
    occurrence = last_occurrence(schedule, today)
    if occurrence is None:
        return False
    return done_date is None or done_date < occurrence


def parse_timestamp(value):
    """A local date from a done_at value: either what this app stored
    (naive local isoformat) or what Postgres sent (UTC offset, and fractional
    seconds that older Pythons' fromisoformat can't always read)."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.date()


def is_due_today(schedule, today):
    """Whether today is a day this item comes due (used for reminders)."""
    if schedule.get("recurrence") == "once":
        return schedule.get("due_date") == today.isoformat()
    return last_occurrence(schedule, today) == today


def reminder_occurrence(schedule, now):
    """The occurrence date (isoformat) whose reminder time has arrived, or
    None. A one-off with a date and time keeps counting as due after that
    moment (so a reminder still fires if the app was closed at the time); a
    repeating item only reminds on its own day, like the personal checklist."""
    due_time = schedule.get("due_time")
    if not due_time:
        return None
    today = now.date()
    if schedule.get("recurrence") == "once":
        due_date = schedule.get("due_date")
        if not due_date:
            return None
        return due_date if now.strftime("%Y-%m-%d %H:%M") >= f"{due_date} {due_time}" else None
    if last_occurrence(schedule, today) == today and now.strftime("%H:%M") >= due_time:
        return today.isoformat()
    return None


def is_overdue(schedule, now):
    """A one-off whose date (and time, else end of that day) has passed."""
    if schedule.get("recurrence") != "once" or not schedule.get("due_date"):
        return False
    try:
        due_day = date.fromisoformat(schedule["due_date"])
    except ValueError:
        return False
    hour, minute = (int(p) for p in (schedule.get("due_time") or "23:59").split(":"))
    return now > datetime.combine(due_day, time(hour, minute))


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def describe(schedule, today=None):
    """Short human text for the list, e.g. "🔁 Every Tuesday at 08:00",
    "🔁 Monthly on the 15th", "📅 Fri 25 Sep at 18:00". Empty for a plain item."""
    s = normalize(schedule)
    at = f" at {s['due_time']}" if s["due_time"] else ""
    if s["recurrence"] == "daily":
        return f"\U0001F501 Every day{at}"
    if s["recurrence"] == "weekly":
        return f"\U0001F501 Every {WEEKDAY_NAMES[s['weekday']]}{at}"
    if s["recurrence"] == "monthly":
        return f"\U0001F501 Monthly on the {_ordinal(s['month_day'])}{at}"
    if s["due_date"]:
        try:
            day = date.fromisoformat(s["due_date"])
        except ValueError:
            return ""
        today = today or date.today()
        fmt = "%a %d %b" if day.year == today.year else "%a %d %b %Y"
        return f"\U0001F4C5 {day.strftime(fmt)}{at}"
    return ""
