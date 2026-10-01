"""
exporter.py - "Export everything…": one zip with every notes page as
Markdown, the checklist as Markdown, and the full data file (minus login
tokens), which View → Restore from a backup… → "Choose a file…" can load
back in.
"""
import json
import re
import zipfile
from datetime import date

from PyQt6.QtGui import QTextDocument

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DATA_NAME = "pawmodoro-data.json"


def html_to_markdown(stored):
    """Notes are stored as Qt rich-text HTML (or plain text from old versions)."""
    doc = QTextDocument()
    if "<" in stored:
        doc.setHtml(stored)
    else:
        doc.setPlainText(stored)
    return doc.toMarkdown()


def _safe_filename(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().rstrip(".")
    return name or "page"


def checklist_markdown(tasks):
    lines = ["# Checklist", ""]
    for task in tasks:
        if task.get("source") == "wishlist":
            continue
        box = "x" if task.get("completed_today") else " "
        when = WEEKDAYS[task["weekday"]] if task["recurrence"] == "weekday" and task.get("weekday") is not None else task["recurrence"]
        extra = [when]
        if task.get("reminder_every_h"):
            extra.append(f"reminder every {task['reminder_every_h']}h")
        elif task.get("reminder_time"):
            extra.append(f"reminder {task['reminder_time']}")
        if task.get("focus_pomodoros"):
            extra.append(f"{task['focus_pomodoros']} pomodoros")
        lines.append(f"- [{box}] {task['text']} ({', '.join(extra)})")
    wishlist = [t for t in tasks if t.get("source") == "wishlist"]
    if wishlist:
        lines += ["", "## Card wishlist", ""]
        lines += [f"- [{'x' if t.get('completed_today') else ' '}] {t['text']}" for t in wishlist]
    return "\n".join(lines) + "\n"


def export_zip(storage, path):
    """Writes the export to `path`; returns the number of notes pages in it."""
    pages = storage.get_note_pages()
    used = set()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for number, page in enumerate(pages, start=1):
            name = f"{number:02d} {_safe_filename(page['title'])}.md"
            while name in used:
                name = name[:-3] + "_.md"
            used.add(name)
            zf.writestr(f"notes/{name}", html_to_markdown(storage.get_page_html(page["id"])))
        zf.writestr("checklist.md", checklist_markdown(storage.get_checklist()))
        zf.writestr(DATA_NAME, json.dumps(storage.export_data(), indent=2))
        zf.writestr("README.txt", (
            f"Exported from Pawmodoro on {date.today().isoformat()}.\n\n"
            "notes/        every notes page, as Markdown\n"
            "checklist.md  your checklist\n"
            f"{DATA_NAME}  everything (no login details); load it back with\n"
            "              View > Restore from a backup... > Choose a file...\n"
        ))
    return len(pages)
