"""
gamification.py - Pure logic for Pawmodoro's leveling, XP, and daily/weekly
quest system. No Qt or storage dependencies here, so the math and quest
picking are easy to reason about (and change) on their own.
"""
import random
from datetime import timedelta

XP_PER_WORK_SESSION = 20
XP_PER_TASK = {"daily": 10, "weekly": 15, "once": 25, "weekday": 15}
QUEST_BONUS_XP = 30
ALL_QUESTS_BONUS_XP = 50
QUESTS_PER_DAY = 3

WEEKLY_QUEST_BONUS_XP = 80
ALL_WEEKLY_QUESTS_BONUS_XP = 150
QUESTS_PER_WEEK = 3
# Weekly quests reset every Tuesday.
WEEK_RESET_WEEKDAY = 1  # Monday=0, Tuesday=1, ...

# A title shown alongside the level number, unlocked as you climb. The
# highest threshold at or below the current level wins.
TITLES = [
    (1, "Curious Pup"),
    (3, "Eager Fox"),
    (6, "Focused Hound"),
    (10, "Diligent Doe"),
    (15, "Steady Stag"),
    (20, "Tireless Tabby"),
    (30, "Productivity Panther"),
    (40, "Zen Owl"),
    (50, "Legendary Loremaster"),
]

QUEST_POOL = [
    {"id": "pomodoros_2", "kind": "pomodoros", "target": 2, "desc": "Complete 2 pomodoro sessions"},
    {"id": "pomodoros_4", "kind": "pomodoros", "target": 4, "desc": "Complete 4 pomodoro sessions"},
    {"id": "pomodoros_6", "kind": "pomodoros", "target": 6, "desc": "Complete 6 pomodoro sessions"},
    {"id": "tasks_2", "kind": "tasks", "target": 2, "desc": "Finish 2 checklist tasks"},
    {"id": "tasks_3", "kind": "tasks", "target": 3, "desc": "Finish 3 checklist tasks"},
    {"id": "tasks_5", "kind": "tasks", "target": 5, "desc": "Finish 5 checklist tasks"},
    {"id": "breaks_1", "kind": "breaks", "target": 1, "desc": "Take at least one break"},
    {"id": "breaks_2", "kind": "breaks", "target": 2, "desc": "Take 2 breaks"},
    {"id": "clear_checklist", "kind": "clear_checklist", "target": 1, "desc": "Clear your whole checklist for today"},
]

WEEKLY_QUEST_POOL = [
    {"id": "week_pomodoros_15", "kind": "pomodoros", "target": 15, "desc": "Complete 15 pomodoro sessions this week"},
    {"id": "week_pomodoros_25", "kind": "pomodoros", "target": 25, "desc": "Complete 25 pomodoro sessions this week"},
    {"id": "week_pomodoros_35", "kind": "pomodoros", "target": 35, "desc": "Complete 35 pomodoro sessions this week"},
    {"id": "week_tasks_15", "kind": "tasks", "target": 15, "desc": "Finish 15 checklist tasks this week"},
    {"id": "week_tasks_25", "kind": "tasks", "target": 25, "desc": "Finish 25 checklist tasks this week"},
    {"id": "week_breaks_5", "kind": "breaks", "target": 5, "desc": "Take 5 breaks this week"},
    {"id": "week_breaks_8", "kind": "breaks", "target": 8, "desc": "Take 8 breaks this week"},
    {"id": "week_clear_checklist_3", "kind": "clear_checklist", "target": 3, "desc": "Clear your whole checklist 3 times this week"},
]


def title_for_level(level):
    result = TITLES[0][1]
    for threshold, name in TITLES:
        if level >= threshold:
            result = name
    return result


def xp_for_next_level(level):
    """XP needed to go from `level` to `level + 1`. Increases gently so
    early levels come quickly and later ones take real, sustained use."""
    return 80 + (level - 1) * 20


def level_from_xp(total_xp):
    """Returns (level, xp_into_current_level, xp_needed_for_next_level)."""
    level = 1
    remaining = total_xp
    needed = xp_for_next_level(level)
    while remaining >= needed:
        remaining -= needed
        level += 1
        needed = xp_for_next_level(level)
    return level, remaining, needed


def generate_daily_quests(day_iso, count=QUESTS_PER_DAY):
    """Deterministic per-day selection (seeded by the date string) so
    restarting the app on the same day doesn't reshuffle quests already
    in progress."""
    rng = random.Random(day_iso)
    chosen = rng.sample(QUEST_POOL, k=min(count, len(QUEST_POOL)))
    return [
        {
            "id": template["id"],
            "kind": template["kind"],
            "target": template["target"],
            "desc": template["desc"],
            "progress": 0,
            "completed": False,
        }
        for template in chosen
    ]


def week_start_for(d):
    """Returns the date of the Tuesday that starts `d`'s current quest
    week (today itself, if today is a Tuesday)."""
    days_since_reset = (d.weekday() - WEEK_RESET_WEEKDAY) % 7
    return d - timedelta(days=days_since_reset)


def days_until_weekly_reset(d):
    """Days remaining until the next Tuesday reset (7 if `d` is a Tuesday,
    since that reset has already happened for today)."""
    return 7 - ((d.weekday() - WEEK_RESET_WEEKDAY) % 7)


def generate_weekly_quests(week_start_iso, count=QUESTS_PER_WEEK):
    """Deterministic per-week selection (seeded by the Tuesday that starts
    the week) so restarting the app mid-week doesn't reshuffle quests
    already in progress."""
    rng = random.Random(week_start_iso)
    chosen = rng.sample(WEEKLY_QUEST_POOL, k=min(count, len(WEEKLY_QUEST_POOL)))
    return [
        {
            "id": template["id"],
            "kind": template["kind"],
            "target": template["target"],
            "desc": template["desc"],
            "progress": 0,
            "completed": False,
        }
        for template in chosen
    ]


def task_already_awarded(recurrence, awarded_on, today):
    """A task pays out XP / quest progress only the first time it's
    completed in its period, so un-checking and re-checking it can't be
    farmed: a "once" task pays out ever, a "weekly" task once per quest
    week, everything else (daily / specific-day) once per day.
    `awarded_on` is an ISO date string (or None); `today` is a date.
    Mirrored by complete_task() in supabase/schema.sql."""
    if not awarded_on:
        return False
    if recurrence == "once":
        return True
    if recurrence == "weekly":
        return awarded_on >= week_start_for(today).isoformat()
    return awarded_on == today.isoformat()


def weekly_task_needs_reset(last_completed, today):
    """A "weekly" task un-checks itself when the quest week rolls over
    (Tuesday), same reset as the weekly quests. `last_completed` is an ISO
    date string or None. Mirrored by roll_recurring_tasks() in
    supabase/schema.sql."""
    return not last_completed or last_completed < week_start_for(today).isoformat()
