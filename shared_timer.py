"""
shared_timer.py - The rules for one pomodoro timer across devices (no Qt,
no storage; PomodoroTab applies them). The device that last started,
paused, reset or skipped the timer is its owner; the others follow it.
Mirrored by the phone's followRemoteTimer / creditPhase / takeOverTimer in
docs/app.js -- keep them in step (see CLAUDE.md, "One timer across devices").
"""

# A follower whose phase ran out waits this long for the owner to report
# the next phase before counting the session itself (TIMER_TAKEOVER_MS).
TAKEOVER_SECONDS = 90
# A running timer that ended longer ago than this was left running
# somewhere long ago: there's nothing to follow.
STALE_SECONDS = 60


def session_key(phase, ended_at):
    """What a finished phase is counted under, so it counts once however
    many devices report it: "<phase>:<round(ends_at)>". None without an
    end time (a skipped phase, or one that never ran against the clock)."""
    return f"{phase}:{round(ended_at)}" if ended_at else None


def counts_here(completed, owner, sharing):
    """Whether this device credits a phase that just ended. Only the owner
    does; a follower leaves it to the owner (and may take over later)."""
    return not completed or owner or not sharing


def chained_end(ended_at, next_seconds, now):
    """When an auto-started next phase ends: chained onto the end of the
    last, so every device works out the same end. None (start from now)
    without a previous end, or when that would leave under a second."""
    chained = ended_at + next_seconds if ended_at else None
    return chained if chained and chained > now + 1 else None


def followable(timer, phases, now):
    """Whether another device's timer state is worth following here."""
    if not timer or timer.get("phase") not in phases:
        return False
    ends_at = timer.get("ends_at")
    return not (timer.get("running") and ends_at and ends_at < now - STALE_SECONDS)
