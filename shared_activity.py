"""
shared_activity.py - Pure logic (no Qt, no storage) for "someone else changed
the shared list" notifications: compares the household's tasks as last cached
with what a cloud pull just returned and says what the *other* members did.

Three things are reported:
    added      an item whose id wasn't in the cached list before
    completed  an item that was unticked before and is ticked now
    assigned   an item that's now your turn and wasn't before (v2.20; your
               own assignments are already in the cache, so never show up)

Your own changes never show up here for two reasons: they're already in the
cache when the pull arrives (so nothing is "new" or "newly ticked"), and the
server tells us who did it -- `created_by_me` / `done_by_me` in household_pull
(added in v2.13; on an older cloud schema those are missing, and a tick is
compared by name instead, while an item added from your own other device may
be reported).
"""

MAX_NAMED = 3  # items spelled out in one notification before "and N more"
MAX_LENGTH = 220


def detect(old_tasks, new_tasks, my_name=None):
    """{"added": [{"text", "by"}], "completed": [{"text", "by"}],
    "assigned": [{"text", "by"}]} -- what the other members did between the
    two lists, in list order. `by` is a display name or None (an older cloud
    schema doesn't say who added an item; nobody says who assigned one)."""
    old_by_id = {t["id"]: t for t in old_tasks or []}
    added, completed, assigned = [], [], []
    for task in new_tasks or []:
        before = old_by_id.get(task["id"])
        if before is None:
            if task.get("created_by_me") is True:
                continue
            added.append({"text": task.get("text", ""), "by": task.get("created_by_name")})
            continue
        if task.get("assigned_to_me") and not before.get("assigned_to_me") and not task.get("done"):
            assigned.append({"text": task.get("text", ""), "by": None})
        if task.get("done") and not before.get("done"):
            if task.get("done_by_me") is True:
                continue
            by = task.get("done_by_name")
            if "done_by_me" not in task and by and by == my_name:
                continue
            completed.append({"text": task.get("text", ""), "by": by})
    return {"added": added, "completed": completed, "assigned": assigned}


def _who(entries):
    names = []
    for entry in entries:
        if entry["by"] and entry["by"] not in names:
            names.append(entry["by"])
    if not names:
        return "Someone"
    if len(names) == 1:
        return names[0]
    return " & ".join(names)


def _describe(entries, verb, noun):
    """One notification's text: "Sam added: Milk", or "Sam added 3 items: Milk, Eggs, Bread"."""
    who = _who(entries)
    if len(entries) == 1:
        text = f"{who} {verb}: {entries[0]['text']}"
    else:
        named = ", ".join(e["text"] for e in entries[:MAX_NAMED])
        more = len(entries) - MAX_NAMED
        text = f"{who} {verb} {len(entries)} {noun}s: {named}" + (f" and {more} more" if more > 0 else "")
    return text if len(text) <= MAX_LENGTH else text[: MAX_LENGTH - 1] + "…"


def notifications(changes):
    """[(title, message), ...] for what detect() found: at most one for the
    additions and one for the completions."""
    out = []
    if changes["added"]:
        out.append(("Shared list: new item", _describe(changes["added"], "added", "item")))
    if changes["completed"]:
        out.append(("Shared list: item done", _describe(changes["completed"], "completed", "item")))
    if changes.get("assigned"):
        texts = [e["text"] for e in changes["assigned"]]
        message = "Your turn: " + ", ".join(texts[:MAX_NAMED])
        if len(texts) > MAX_NAMED:
            message += f" and {len(texts) - MAX_NAMED} more"
        out.append(("Shared list: your turn",
                    message if len(message) <= MAX_LENGTH else message[: MAX_LENGTH - 1] + "…"))
    return out
