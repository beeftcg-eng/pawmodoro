"""
parity_cases.py - Writes tests/parity/cases.json: the answers the Python
rules give for a spread of dates and settings, for each rule that
supabase/schema.sql and docs/app.js implement a second (and third) time.

    python tools/parity_cases.py

tests/test_parity.py fails if the file is out of date, tests/sql/run.sh
checks the schema's functions against it and tests/pwa the phone's, so a
rule changed in one place and not the others fails a test somewhere.
"""
import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import calendar_view  # noqa: E402
import gamification  # noqa: E402
import shared_schedule  # noqa: E402
import storage  # noqa: E402
import task_dates  # noqa: E402

CASES_FILE = os.path.join(ROOT, "tests", "parity", "cases.json")

# Month ends, a leap day, a year boundary and an ordinary stretch.
DATES = [date(2026, 10, 1) + timedelta(days=n) for n in range(21)] + [
    date(2024, 2, 28), date(2024, 2, 29), date(2024, 3, 1),
    date(2026, 1, 30), date(2026, 1, 31), date(2026, 2, 27), date(2026, 2, 28), date(2026, 3, 1),
    date(2026, 3, 30), date(2026, 3, 31), date(2026, 4, 30),
    date(2026, 12, 29), date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 5),
]
MONTH_DAYS = [1, 15, 28, 29, 30, 31]


def iso(d):
    return d.isoformat() if d else None


def build():
    cases = {"week_start": [], "month_occurrence": [], "reminds": [], "task_reset": [],
             "shared_occurrence": [], "quiet_hours": [], "daily_quests": [], "weekly_quests": [],
             "done_days": [], "streak": [], "calendar_task": [], "calendar_shared": []}

    for d in DATES:
        cases["week_start"].append({"day": iso(d), "expect": iso(gamification.week_start_for(d))})
        for month_day in MONTH_DAYS:
            cases["month_occurrence"].append(
                {"month_day": month_day, "day": iso(d), "expect": iso(task_dates.month_occurrence(month_day, d))})

    tasks = [{"recurrence": r} for r in ("daily", "weekly", "once")]
    tasks += [{"recurrence": "monthly", "month_day": m} for m in MONTH_DAYS]
    tasks += [{"recurrence": "once", "due_date": due} for due in ("2026-10-05", "2026-02-28", "2027-01-01")]
    for d in DATES:
        for task in tasks:
            cases["reminds"].append({"task": task, "day": iso(d), "expect": task_dates.reminds_today(task, d)})

    # A ticked task's last completion, a few days before each date.
    reset_tasks = [{"recurrence": r} for r in ("daily", "weekly", "once")]
    reset_tasks += [{"recurrence": "monthly", "month_day": m} for m in (1, 15, 31)]
    for d in DATES:
        for task in reset_tasks:
            for back in (None, 0, 1, 7, 30):
                last = None if back is None else iso(d - timedelta(days=back))
                full = dict(task, last_completed=last, completed_today=True)
                cases["task_reset"].append({"task": full, "day": iso(d), "expect": task_dates.needs_reset(full, d)})

    schedules = [{"recurrence": "daily"}, {"recurrence": "once"}]
    schedules += [{"recurrence": "weekly", "weekday": w} for w in (0, 1, 3, 6)]
    schedules += [{"recurrence": "monthly", "month_day": m} for m in MONTH_DAYS]
    for d in DATES:
        for schedule in schedules:
            cases["shared_occurrence"].append(
                {"schedule": schedule, "day": iso(d), "expect": iso(shared_schedule.last_occurrence(schedule, d))})

    windows = [(True, "22:00", "08:00"), (True, "09:00", "17:00"), (True, "08:00", "08:00"), (False, "22:00", "08:00")]
    for enabled, start, end in windows:
        for hm in ("00:00", "07:59", "08:00", "09:00", "12:30", "16:59", "17:00", "21:59", "22:00", "23:59"):
            settings = {"quiet_enabled": enabled, "quiet_start": start, "quiet_end": end}
            cases["quiet_hours"].append({"settings": settings, "hm": hm, "expect": storage.in_quiet_hours(settings, hm)})

    # Ticking / un-ticking a task (its done_days), and daily streaks.
    today = date(2026, 10, 12)
    histories = {
        "none": [],
        "run": [iso(today - timedelta(days=n)) for n in range(5)],
        "run_to_yesterday": [iso(today - timedelta(days=n)) for n in range(1, 4)],
        "gap": [iso(today - timedelta(days=n)) for n in (0, 1, 3, 4)],
        "old_and_unsorted": [iso(today - timedelta(days=n)) for n in (200, 119, 120, 2, 1)],
        "long": [iso(today - timedelta(days=n)) for n in range(1, 130)],
    }
    for name, days in histories.items():
        for done in (True, False):
            cases["done_days"].append({"days": days, "day": iso(today), "done": done,
                                       "expect": task_dates.record_done_day(days, today, done)})
        for back in (0, 1, 2):
            day = today + timedelta(days=back)
            task = {"recurrence": "daily", "done_days": days}
            cases["streak"].append({"task": task, "day": iso(day), "expect": task_dates.streak(task, day)})
    cases["streak"].append({"task": {"recurrence": "weekly", "done_days": histories["run"]},
                            "day": iso(today), "expect": 0})

    # What the Calendar tab puts on a day (calendar_view), and whether it's ticked there.
    today = date(2026, 10, 12)
    cal_tasks = [{"recurrence": r} for r in ("daily", "weekly", "once")]
    cal_tasks += [{"recurrence": "once", "due_date": due, "completed_today": done}
                  for due in ("2026-10-05", "2026-02-28") for done in (False, True)]
    cal_tasks += [{"recurrence": "monthly", "month_day": m, "done_days": ["2026-10-01", "2026-09-30", "2026-02-28"]}
                  for m in (1, 15, 28, 31)]
    cal_tasks += [{"recurrence": "once", "due_date": "2026-10-05", "source": "wishlist"}]
    for d in DATES:
        for task in cal_tasks:
            cases["calendar_task"].append({"task": task, "day": iso(d), "expect": {
                "on": calendar_view.task_on_day(task, d), "done": calendar_view.task_done_on(task, d)}})
    cal_shared = [{"recurrence": "daily", "done": True}, {"recurrence": "once", "done": False},
                  {"recurrence": "once", "due_date": "2026-10-05", "done": True}]
    cal_shared += [{"recurrence": "weekly", "weekday": w, "done": done} for w in (0, 3, 6) for done in (False, True)]
    cal_shared += [{"recurrence": "monthly", "month_day": m, "done": True} for m in (1, 12, 31)]
    for d in DATES:
        for task in cal_shared:
            cases["calendar_shared"].append({"task": task, "day": iso(d), "today": iso(today), "expect": {
                "on": calendar_view.shared_on_day(task, d), "done": calendar_view.shared_done_on(task, d, today)}})

    for d in DATES:
        cases["daily_quests"].append(
            {"day": iso(d), "expect": [q["id"] for q in gamification.generate_daily_quests(iso(d))]})
    for start in sorted({gamification.week_start_for(d) for d in DATES}):
        cases["weekly_quests"].append(
            {"week_start": iso(start), "expect": [q["id"] for q in gamification.generate_weekly_quests(iso(start))]})
    return cases


def render():
    """One case per line, so a diff shows which answers changed."""
    lines = []
    for rule, rows in sorted(build().items()):
        body = ",\n".join("  " + json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows)
        lines.append(f"{json.dumps(rule)}: [\n{body}\n]")
    return "{\n" + ",\n".join(lines) + "\n}\n"


if __name__ == "__main__":
    with open(CASES_FILE, "w", encoding="utf-8") as f:
        f.write(render())
    print(f"wrote {os.path.relpath(CASES_FILE, ROOT)}")
