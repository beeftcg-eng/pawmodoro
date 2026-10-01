#!/usr/bin/env python3
"""
release_notes.py - Prints the GitHub release notes for the current
version.VERSION: the README's "What changed" entry for that version (the
README is the changelog, see CLAUDE.md), plus how to update.

    python tools/release_notes.py > notes.md

Exits with an error if the README has no entry for this version, so a
release can't go out with the changelog forgotten.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from version import VERSION  # noqa: E402

UPDATING = """## Updating

- **From v2.12.0 or later:** click **⬆ Update to v{version}** in the window header, then **Update now** (or **View → Check for updates…**).
- **First install:** Windows: run `Pawmodoro-Setup.exe`, or extract the zip and double-click `install.bat`. Linux: extract the zip and run `./install.sh`.
"""


def changelog_entry(readme_text, version):
    """The README entry starting with **v<version> and running up to the
    next **v entry (or the end of the changelog section)."""
    start = re.search(rf"^\*\*v{re.escape(version)}(?![\w.]).*$", readme_text, re.MULTILINE)
    if start is None:
        return None
    rest = readme_text[start.start():]
    end = re.search(r"^(\*\*v\d|## )", rest[2:], re.MULTILINE)
    return (rest[:end.start() + 2] if end else rest).strip()


def notes(readme_text, version):
    entry = changelog_entry(readme_text, version)
    if entry is None:
        raise SystemExit(f"README.md has no 'What changed' entry for v{version}")
    return ("Ready-to-run bundle for Windows (and Linux), including the ambient sound tracks.\n\n"
            f"{entry}\n\n{UPDATING.format(version=version)}")


def main():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        print(notes(f.read(), VERSION))


if __name__ == "__main__":
    main()
