#!/usr/bin/env python3
"""
check_cloud_defaults.py - Checks that every app connecting to the shared
Supabase project ships the same URL and anon key as cloud_defaults.py, and
that the key really is the `anon` role (see CLAUDE.md, "Shared Supabase
project defaults").

    python tools/check_cloud_defaults.py            # this repo only
    python tools/check_cloud_defaults.py --remote   # plus the other apps

With --remote, each other app is read from its checkout under ~/Desktop
when there is one, else from the public GitHub repo. Exits with an error
listing every copy that differs (or that couldn't be read).
"""
import base64
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import cloud_defaults  # noqa: E402

DESKTOP = os.path.expanduser("~/Desktop")

# (name, local checkout, GitHub "owner/repo/branch", path inside it)
OTHER_COPIES = [
    ("Deckbuilder", "deckbuilder", "beeftcg-eng/deckbuilder/master", "src/shared/pawmodoroDefaults.ts"),
    ("Pairings", "pairings", "beeftcg-eng/Pairings/main", "index.html"),
    ("Dungeons Desk", "DmTools", "beeftcg-eng/dungeons-desk/main", "shared/cloud.js"),
]

# (a "https://xxxx.supabase.co" placeholder in a hint doesn't count)
URL_RE = re.compile(r"https://(?!x+\.)[a-z0-9]+\.supabase\.co")
KEY_RE = re.compile(r"eyJ[\w-]+\.eyJ[\w-]+\.[\w-]+")


def jwt_role(key):
    payload = key.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload)).get("role")


def problems(name, text):
    """What's wrong with the copy in `text` (empty when it matches)."""
    found = []
    urls = set(URL_RE.findall(text))
    keys = set(KEY_RE.findall(text))
    if urls != {cloud_defaults.DEFAULT_URL}:
        found.append(f"{name}: URL {sorted(urls) or 'missing'}, expected {cloud_defaults.DEFAULT_URL}")
    if keys != {cloud_defaults.DEFAULT_ANON_KEY}:
        found.append(f"{name}: anon key differs from cloud_defaults.py" if keys else f"{name}: anon key missing")
    return found


def read_other(local, repo, path):
    local_path = os.path.join(DESKTOP, local, path)
    if os.path.isfile(local_path):
        with open(local_path, encoding="utf-8") as f:
            return f.read(), local_path
    url = f"https://raw.githubusercontent.com/{repo}/{path}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return resp.read().decode("utf-8"), url


def check(remote):
    found = []
    if jwt_role(cloud_defaults.DEFAULT_ANON_KEY) != "anon":
        found.append("cloud_defaults.py: the key is not the anon role")
    with open(os.path.join(ROOT, "docs", "app.js"), encoding="utf-8") as f:
        found += problems("docs/app.js", f.read())
    if remote:
        for name, local, repo, path in OTHER_COPIES:
            try:
                text, where = read_other(local, repo, path)
            except OSError as e:
                found.append(f"{name}: couldn't read {path} ({e})")
                continue
            print(f"checked {name}: {where}")
            found += problems(name, text)
    return found


def main():
    found = check("--remote" in sys.argv[1:])
    for line in found:
        print(line, file=sys.stderr)
    if found:
        sys.exit(1)
    print("cloud defaults match")


if __name__ == "__main__":
    main()
