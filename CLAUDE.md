# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Pawmodoro is a desktop app (Python + PyQt6) combining notes, a recurring checklist, a pomodoro timer, ambient sounds, YouTube Music/Spotify controls, and gamification (XP/levels/quests). It runs natively on both Linux and Windows from one codebase. There's also a companion mobile PWA (`docs/`) and an optional Supabase cloud-sync backend (`supabase/schema.sql`) that keeps notes/checklist/gamification in sync between desktop and phone.

There is no test suite in this repo — changes are verified by running the app manually (see below) and, per the README's own convention, by explicit "honest testing notes" on anything that couldn't be exercised live (no Windows machine, no Spotify account, no display in the dev sandbox).

## Running / developing

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # PyQt6, PyQt6-WebEngine
python3 main.py
```

There's no build step, linter, or test command configured — this is a straight-run PyQt6 app. `install.sh` (Linux) / `install.ps1` + `install.bat` (Windows) set up an isolated venv under the user's app-data dir for end users; they're not needed for development from source.

Ambient sound `.wav`/`.ogg` files under `resources/` are gitignored (one is 227MB) and must be regenerated locally before ambient sounds will work from a fresh clone:
```bash
pip install numpy scipy
python tools/generate_rain.py && python tools/generate_ambient.py
```

App data (all tabs' state) lives in a single JSON file, not in the repo: `~/.local/share/pawmodoro/data.json` on Linux, `%APPDATA%\Pawmodoro\data.json` on Windows (path logic in `paths.py`).

## Architecture

### Storage is the single source of truth

`storage.py`'s `Storage` class owns one in-memory dict (`self.data`, seeded from `DEFAULT_DATA`) that is the entire app state — notes, checklist, pomodoro settings, window/widget geometry, theme, custom/hidden ambient sounds, Spotify tokens, gamification, sync config. Every mutator method (`set_notes`, `add_task`, `set_task_done`, `add_xp`, ...) writes to this dict and calls `self.save()` immediately (atomic write via a `.tmp` file + `os.replace`), so nothing is batched or lost on crash. Every tab widget is constructed with a reference to the same `Storage` instance (see `main.py`'s `MainWindow.__init__`) and reads/writes through it rather than holding its own state — this is the pattern to follow for any new persisted feature.

### Tab structure

`main.py`'s `MainWindow` hosts a `QTabWidget` with `NotesChecklistTab`, `PomodoroTab`, `ProgressTab`, `MusicTab`, plus an always-visible `PlayerBar` docked below the tabs. `NotesChecklistTab` (`notes_checklist_tab.py`) is itself a composite: `NotesTab` + `ChecklistTab` in a horizontal splitter — the Card Wishlist section inside the checklist can be reordered independently of the regular checklist (see recent commits). `WidgetWindow` (`widget_window.py`) is a separate frameless always-on-top window showing a condensed view (pending tasks, timer, mini player) for "pinned widget mode" — entering/leaving that mode hides the main window rather than closing it (see `_enter_widget_mode` / `restore_from_widget` in `main.py`).

Cross-tab effects go through explicit Qt signals wired up in `MainWindow`, not through tabs reaching into each other directly: e.g. `pomodoro_tab.celebrate` / `checklist_tab.celebrate` both feed `_on_celebrate` (shows the `CelebrationToast`, refreshes XP/level display and the Progress tab), and `pomodoro_tab.tick` feeds `widget_window.update_pomodoro`.

### Theming

`theme.py` defines named palettes (Paper, Lavender Dusk, Deep Plum, Royal Purple, Charcoal Grey, Slate & Mauve, plus two "gamer" themes) and builds a global Qt stylesheet. Switching themes (`MainWindow._apply_theme`) sets the app-wide stylesheet *and* calls `refresh_theme()` on every tab/widget individually, because some widgets draw custom (non-stylesheet-driven) elements that need an explicit repaint. Any new widget with custom-painted chrome needs its own `refresh_theme()` hooked into this same fan-out.

### Gamification

`gamification.py` is pure logic (no Qt, no storage) — XP totals, level curve, streaks, and daily/weekly quest generation/selection. Quest lists are deterministically seeded per day/week (`random.Random(day_iso)` / `random.Random(week_start_iso)`) so restarting the app mid-day doesn't reshuffle in-progress quests. `storage.py` calls into this module and is the only place that mutates the persisted gamification state; `supabase/schema.sql` independently reimplements the *same* XP/level/quest math in SQL/PLpgSQL so the server can be authoritative when synced — **when changing XP amounts, level curve, or quest pools in `gamification.py`, mirror the change in `supabase/schema.sql`, or desktop-offline and cloud-synced behavior will diverge.**

### Cloud sync (optional, off by default)

Three pieces that must be kept in sync with each other when the sync protocol changes:
- `supabase/schema.sql` — Postgres schema + RPC functions (`sync_pull`, `add_task`, `set_task_completed_flag`, `apply_task_xp`, `record_pomodoro_completed`, `reorder_tasks`, etc.), idempotent (safe to re-run in full — if-not-exists / drop-then-create everywhere) and RLS-scoped per user. Every sync function pins its `search_path` explicitly (defense against a hostile/misconfigured PostgREST `search_path`).
- `supabase_sync.py` — the desktop client (stdlib `urllib` only, no new dependency), one thin method per RPC. Every network call has a short timeout (`TIMEOUT = 5`) and raises `SyncError` uniformly; nothing else in the desktop app ever sees a raw `urllib` exception.
- `docs/app.js` — the mobile PWA's client, calling the same RPCs directly via the Supabase JS SDK.

`storage.py` treats sync as strictly best-effort: every sync-aware mutator (e.g. `add_task`, `set_task_done`) does the local write/save unconditionally first, then *also* tries the remote call wrapped in `try/except SyncError: pass` — a dead/misconfigured backend must never block or corrupt local state. `_remote_call` is the shared helper for gamification events: on success it re-pulls full server state and overwrites the local cache (server is authoritative once connected); on any failure it returns `None` so the caller falls back to local-only math. Supabase rotates the refresh token on every use, so `_persist_sync_token_if_changed` runs after every sync operation to keep the on-disk token from going stale.

One recurrence value, `"weekday"` (a task pinned to a specific day of the week, e.g. trash day), is desktop-only — the cloud schema's `CHECK` constraint on `checklist_tasks.recurrence` doesn't allow it, so all sync paths (`add_task`, `reorder_tasks`, `record_task_event`) explicitly skip the remote call for these tasks and `_apply_remote_state` preserves them across a full state replace rather than letting a pull silently delete them.

The mobile PWA (`docs/`) is a static site auto-deployed via GitHub Pages from this repo; it talks directly to Supabase and has no dependency on the Python code at runtime — only on the RPC contract in `supabase/schema.sql` staying compatible with both clients.

### Cross-platform seams

Platform differences are isolated behind small dedicated modules rather than scattered `if platform.system()` checks in feature code:
- `paths.py` — app data directory per OS.
- `notifier.py` — desktop notifications via `QSystemTrayIcon.showMessage` (works on both OSes, no external dependency).
- `ambient_loop.py` — ambient sound playback; prefers `ffplay` on both platforms, falls back to `paplay`/`pw-play`/`aplay` on Linux or Qt's `QSoundEffect` on Windows.
- `mpris.py` (Linux, via `playerctl`) / `smtc_windows.py` (Windows, via System Media Transport Controls) — external media player control for the player bar, both feeding the same `PlayerBar`/`WidgetWindow` UI.

### Windows packaging

`windows_launcher/` contains a small C launcher (`launcher.c`, built via `build.sh`) that produces `Pawmodoro.exe` — used so the Windows Desktop shortcut can launch without a visible console window, distinct from `install.ps1`/`install.bat` which set up the actual Python venv.
