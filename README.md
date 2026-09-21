# Pawmodoro

A small desktop app (Python + PyQt6) with a pen-and-paper look, running natively on **both Linux and Windows** from the same codebase:

1. **Notes & Checklist** — one tab, side by side. Notes are rich text (bold/underline/text color/highlighting/headings/lists) and autosave as you type. The checklist supports `daily` / `weekly` / `once` / **specific day** recurring tasks — daily ones (like "walk the dogs") automatically un-check themselves each new day, and a "specific day" task (like "take out the trash" on your collection day — pick the day from the dropdown that appears) un-checks itself the next time that weekday comes around. Plain `weekly` tasks un-check themselves each Tuesday, when the weekly quests reset. Double-click a task to rename it, and a misclicked delete can be undone for ten seconds. Drag tasks up/down to reorder them (the specific-day setting is desktop-only, though the task order does sync). Notes have a Ctrl+F find bar.
2. **Pomodoro timer** — configurable work/short-break/long-break durations and session count, with desktop notifications, a chime when a phase ends, and optional auto-advance. It counts against a fixed end time (so it can't drift, and doesn't credit a session if the computer sleeps mid-way). Skip moves on without earning XP. An option plays your ambient mix only during work sessions and silences it for breaks.
3. **Ambient sounds** — six built-in tracks (Rain, Ocean Waves, White Noise, Wind, Café Ambience, Fireplace), pick up to 3 to play together, plus add your own audio files. All six built-ins are synthesized locally, not sampled — no copyright concerns.
4. **Music** — a button to open YouTube Music in your regular browser, plus optional Spotify Connect integration if you want it. A player bar at the bottom controls whichever one you're using.
5. **Eight color themes** — Paper, Lavender Dusk, Deep Plum, Royal Purple, Charcoal Grey, Slate & Mauve, plus two "gamer" themes (Hacker Terminal, Arcade Neon) with a monospace font and neon palette — switchable live from a button always visible in the header.
6. **Pinnable, resizable desktop widget mode** — a small, frameless, always-on-top window showing today's pending tasks (tick them off right there), the pomodoro countdown, and mini playback controls. Drag it anywhere, resize it from the corner; both are remembered. *(On Wayland an app can't keep itself above other windows. In KDE, click the widget and press Alt+F3 → More Actions → Keep Above Others, or make it permanent with a rule in System Settings → Window Management → Window Rules.)*
7. **App icon** — a paper-badge icon with a paw print and pen-nib accent, used for the window, taskbar, and tray (and a proper `.ico` for the Windows shortcut).
8. **Gamification** — XP and levels, daily quests, and weekly quests that reset every Tuesday, tracked on the Progress tab alongside a quote that changes each time you visit it, plus a 14-day focus-minutes chart. A task pays out XP only the first time it's completed in its period (un-ticking and re-ticking can't be farmed).
9. **Optional cloud sync + a mobile web app** — notes, checklist, and quest/XP progress can sync through a Supabase backend, and a PWA at [beeftcg-eng.github.io/pawmodoro](https://beeftcg-eng.github.io/pawmodoro/) installs on a phone home screen (iOS or Android) for a lightweight mobile version. Off by default; see [MOBILE_SYNC.md](MOBILE_SYNC.md) for setup. Sync never makes the app wait: edits are saved locally first and uploaded in the background, and anything you change while offline is kept and sent when the connection returns.
10. **A shared household list** — the **🏠 Shared** tab (and phone tab) is a to-do list two people share, for groceries and chores, with who ticked each item off. Items can be **dragged to reorder** and **scheduled** — a one-off with an exact date and time, or repeating daily, weekly on a chosen day ("every Tuesday") or monthly on a chosen day, optionally at a time (you get a desktop reminder). One of you creates a household and gives the other its invite code. Everything else stays private to each account; the Progress tab also shows each other's level, streak and this-week totals.

All data lives in a single JSON file — `~/.local/share/pawmodoro/data.json` on Linux, `%APPDATA%\Pawmodoro\data.json` on Windows — easy to back up or inspect either way.

## Getting the app

- **Just want to run it (e.g. it was shared with you)?** Grab the zip from the [Releases page](../../releases) — it has everything pre-built, including the ambient sound files — and follow "Installing on Windows" below.
- **Cloning this repo instead?** The `resources/*.wav` ambient sound tracks aren't committed (one alone is 227MB — over GitHub's per-file limit). Regenerate them locally with `pip install numpy scipy` then `python tools/generate_rain.py && python tools/generate_ambient.py`, or just use a Releases zip instead.

## Installing on Windows

If someone's just handing you this zip to try (e.g. as a gift/shared app), this is the path for you:

1. Make sure Python 3 is installed. If `python --version` in a Command Prompt doesn't work, install it from [python.org](https://www.python.org/downloads/) or the Microsoft Store — **check "Add python.exe to PATH"** during setup if the installer asks.
2. Extract this zip somewhere (e.g. your Desktop).
3. Double-click **`install.bat`** inside the extracted folder.
4. It sets up a private copy of everything under `%LOCALAPPDATA%\Pawmodoro` (doesn't touch system Python packages) and adds a **Pawmodoro** shortcut to your Desktop.
5. Double-click that shortcut to launch. Closing the window tucks it into the system tray (bottom-right, may be under the ^ arrow) rather than quitting — right-click the tray icon → Quit to fully exit.
6. Want it on your taskbar? Right-click the Desktop shortcut → **Pin to taskbar**.

**Optional, for the best ambient-sound experience:** install ffmpeg (`winget install ffmpeg` in a Command Prompt, or download from ffmpeg.org and add it to your PATH). Without it, ambient sounds still work via a fallback, but ffmpeg gives fully gapless looping and support for non-WAV custom sound files.

**If something goes wrong:** run `%LOCALAPPDATA%\Pawmodoro\run_console.bat` instead of the Desktop shortcut — it opens a console window showing any error output (the normal shortcut deliberately hides the console for a cleaner double-click experience, which also means errors are invisible there).

**Updating later:** from v2.12.0 the app tells you when a newer release exists — a **⬆ Update to vX** button appears in the window header (and you get one notification per version). Click it to see what's new, then **Update now**: it downloads the release, checks its checksum, unpacks it, runs the installer and restarts itself. Nothing installs without that click. *(View menu → **Check for updates…** checks on demand, and **Check for updates automatically** turns the startup check off.)* A copy run straight from a source folder shows the button but can't replace itself — use `git pull` there.

Or by hand: re-run `install.bat` from a fresh copy of the new zip — it detects and stops any running instance first, then updates in place. From PowerShell, one paste does the download and install:

```powershell
$ProgressPreference = 'SilentlyContinue'   # otherwise Windows PowerShell downloads very slowly
$r = Invoke-RestMethod 'https://api.github.com/repos/beeftcg-eng/pawmodoro/releases/latest'
$a = $r.assets | Where-Object { $_.name -like '*-windows.zip' } | Select-Object -First 1
$zip = Join-Path $env:TEMP $a.name
Invoke-WebRequest $a.browser_download_url -OutFile $zip
$dir = Join-Path $env:TEMP 'pawmodoro-update'
if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
Expand-Archive $zip $dir
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'pawmodoro\install.ps1') -Relaunch
```

**Honest note on this port:** I built and tested this on Linux (no Windows machine available to me), so while the Windows-specific code (paths, process handling, the install script) follows documented, standard patterns and I verified everything I could without a live Windows environment, the install script itself hasn't been run on real Windows. If `install.bat` hits an error, run it once more and copy the exact message back to me — that's usually enough to pinpoint and fix.

## What changed from the first version

**v2.12.0 — check-and-click updates.** No schema change.
- **Update button** (`update_checker.py`, `update_dialog.py`, `main.py`): a few seconds after launch, and every six hours while the app sits in the tray, a background thread asks GitHub for the latest release. If it's newer, a **⬆ Update to vX** button appears in the header and one notification is sent per version. Clicking it shows the release notes; **Update now** downloads the release zip (with a progress bar and Cancel), verifies its size and the SHA-256 GitHub lists for it, unpacks it (refusing any unsafe path), runs the installer script detached, and quits so the installer can replace the app and start the new version. Every step after the check is behind that click, and any failure leaves the running version untouched with the reason shown. The check needs only a short GET to `api.github.com` and sends nothing about you but a `Pawmodoro/<version>` user agent.
- **View menu**: **Check for updates…** and a **Check for updates automatically** switch (saved in `data.json`). `PAWMODORO_DISABLE_UPDATE_CHECK=1` disables the whole thing.
- **Installers**: `install.ps1 -Relaunch` and `install.sh --relaunch` restart Pawmodoro when they finish (used by the button; running them by hand is unchanged). A copy run from a source folder gets the button but can't replace itself.
- **Honest testing note**: the update logic was tested against the real GitHub API (the real release's checksum is read and used) and a local download server (checksum mismatch, truncation, cancel, zip-slip and not-a-zip all refused with nothing left behind); the whole click flow was driven in the real `MainWindow` headless (found → button → notify once → dialog → download → unpack → installer launched → app told to quit, plus the failure and cancel paths); and the real `install.sh --relaunch` was run in a throwaway `HOME` and did install 2.12.0 and bring the app back up. **Not exercised: the Windows side** — `install.ps1 -Relaunch` was only parsed (no errors, parameter recognised) and the PowerShell wrapper that runs it was checked with stub scripts under PowerShell on Linux, but nothing ran on a real Windows machine. Also not run: an update over your real install, or the notification popup appearing.

**v2.11.0 — schedule and re-order the shared list.** *(Needs a one-time re-run of `supabase/schema.sql` — see MOBILE_SYNC.md.)*
- **Re-order**: drag items on the desktop Shared tab (▲▼ on the phone); the order syncs to everyone in the household.
- **Schedules** (`shared_schedule.py`): each shared item can be a one-off with an exact date and/or time, or repeat **daily**, **weekly on a weekday** ("every Tuesday"), or **monthly on a day** (short months use their last day), optionally at a time of day. Set it in the "New item" row when adding, or double-click / **Schedule selected…** later. A repeating item un-ticks itself when it next comes due (the cloud does this on every pull; the desktop also does it at local midnight so it's right offline), **Clear completed** now leaves repeating items alone, and an item with a time sends a desktop notification once when it arrives. One-off items past their date show **⚠ overdue**.
- **Phone app**: shows the schedule, adds scheduled items, and re-orders with ▲▼ (cache bumped to v14).
- **Schema**: new columns on `shared_tasks`, `shared_set_schedule` / `shared_reorder`, `shared_add_task` gained schedule parameters (old signature dropped), `household_pull` returns the schedule and rolls due items. Plain items still upload to an un-updated schema; scheduling/re-ordering waits in the upload queue until it's re-run.
- **Honest testing note**: the schedule rules were checked against each other in Python and in a real local Postgres (11,000+ date/rule combinations agree), and the new SQL was exercised as two separate users with row-level security on, including upgrading from the v2.10 schema and re-running it. The desktop tab, sync client calls and a full headless `MainWindow` were run by scripts, with drag-reorder simulated at the model level (`moveRow`) rather than with a real mouse drag. Not exercised: the phone app in a real browser (only its helper functions were run, in Node), a real Supabase project, an actual desktop notification appearing, or Windows.

**v2.10.0 — connects to the shared project by itself.** No schema change.
- **Zero-setup sync**: the desktop app, the phone app and Deckbuilder now come with the shared Pawmodoro Supabase project's URL and anon key built in (`cloud_defaults.py`, `docs/app.js`), so a friend only enters an email and password (**Create account** the first time). The project fields are tucked behind **Use a different Supabase project** for anyone running their own, and an install that already saved its own project keeps it.
- **Honest testing note**: the desktop dialog was exercised headless, and the built-in URL/key were confirmed to be accepted by the live project (a deliberately wrong login gets a normal "Invalid login credentials"). Not exercised: creating a brand-new account through each app, and the phone app in a real browser.

**v2.9.0 — sync you can trust, plus a shared list.** *(Needs a one-time re-run of `supabase/schema.sql` — see MOBILE_SYNC.md.)*
- **Offline-safe sync** (`sync_engine.py`, `storage.py`): edits apply locally at once and go into a persistent upload queue instead of blocking on the network. Previously an edit made while offline was silently overwritten by the next cloud pull, and every autosave/checkbox click could freeze the window for seconds on a bad connection. A stale pull is now refused rather than applied over fresh work, phone edits never overwrite text you're typing, and the checklist no longer rebuilds itself every 5 seconds (which used to drop your selection).
- **Days roll over at *your* midnight** — daily tasks, quests and streaks now reset while the app sits in the tray (they only reset at launch before), and the cloud uses your local day instead of UTC (which reset everything at 6pm for a UTC-6 timezone).
- **Weekly tasks actually reset** (Tuesday, with the weekly quests). **Reminders** fire when their time has *passed* (not only if the app was awake that exact minute), only once a day, and a specific-day task only reminds on its own day.
- **Fairer XP**: Skip earns nothing, and a task pays once per period, so un-ticking/re-ticking can't inflate XP or quests.
- **Timer** counts against an end time; a **chime** plays at phase end; **auto-start next phase** and **ambient-only-during-work** options; ambient **volumes and mix are remembered**. On the phone, the timer keeps the right time through screen lock, keeps the screen awake while running, and pauses polling when the page is hidden.
- **New**: shared household list, focus-minutes history chart, tick tasks from the desktop widget, rename tasks, undo delete, Ctrl+F in notes, a working widget drag on Wayland, and a daily rolling backup of `data.json` (in a `backups/` folder next to it; a corrupt file is restored from the newest one automatically).
- **Fixed**: connecting sync failed outright ("first sync failed") if you had a specific-day task; new settings added in later versions could crash on an old `data.json`.
- **Honest testing note**: the sync engine, storage rules, schema (against a real Postgres, as separate users with row-level security on), phone app (against that same Postgres) and the full desktop window (headless) were all exercised by scripted tests. Not exercised: a real Supabase project (only stock Postgres with Supabase's `auth` stubbed), real phone browsers, a real Wayland session (window drag / always-on-top), and Windows.

- **Text highlighting added** (`notes_toolbar.py`): a new highlighter button next to text color — five soft highlighter-style swatches (yellow, green, pink, blue, orange), a custom color picker, and a "No highlight" option to clear it.
- **Ambient sounds now actually stop when unchecked, every time** (`ambient_loop.py`): `terminate()` alone doesn't guarantee a process dies — if it didn't respond in ~1.5s, `stop()` now escalates to a forceful kill and confirms the process is actually gone before returning, instead of just asking it to stop and hoping. On Linux this also now signals the whole process group (not just the immediate process), so any helper processes a player spawns get cleaned up too. Verified by deliberately testing against a process that ignores the polite shutdown signal, and confirmed no processes are left running either immediately or after a delay.
- **Player bar now prioritizes an actual YouTube Music tab** (`mpris.py`): with multiple browser tabs playing media (e.g. a regular YouTube video open alongside YouTube Music), the bar used to just grab whichever browser-named MPRIS entry it found first. It now checks each candidate's currently-loaded URL and specifically prefers the one that's `music.youtube.com`, falling back to the old browser-name heuristic only when no tab matches. Verified against a simulated two-tab scenario.

- **Removed the embedded YouTube Music webview entirely** — that was QtWebEngine (a full Chromium instance bundled inside the app), and it kept background network connections alive even while minimized, which was the actual cause of the app's heavy network/resource usage. The Music tab is now just a button that opens YouTube Music in your regular system browser, plus the Spotify Connect panel. `PyQt6-WebEngine` is gone from `requirements.txt` — this alone cuts the install size roughly in half.
- **The player bar now controls a real browser tab or Spotify** — since there's no more embedded page to drive via JavaScript, the bottom bar (and widget-mode mini player) go back to controlling whatever's actually playing: Spotify directly (if connected) or a real browser tab via `playerctl`/MPRIS on Linux.
- **Fixed: window couldn't shrink past however many ambient sounds you'd added** — the ambient sounds list was added straight into its panel, so the more sounds you had (especially custom ones), the taller the minimum window size became, eventually preventing you from resizing smaller at all. It's now in a scrollable, height-capped area instead — the sound count no longer affects how small the window can go.

- **Ported to run natively on Windows too**, from the same codebase:
  - `ambient_loop.py` no longer shells out to `bash` (which Windows doesn't have) — replaced with a pure-Python threading loop that works identically on both platforms, and is actually simpler than before. On Windows without ffmpeg installed, it falls back to PyQt6's own `QSoundEffect`, which — unlike on Linux — tends to work well there (bundled Windows Media Foundation backend).
  - Desktop notifications (`notifier.py`) now go through the app's own system tray icon (`QSystemTrayIcon.showMessage`) instead of the Linux-only `notify-send` command — works on Windows and Linux with no extra dependencies.
  - App data now lives in the right place per OS (`paths.py`): `~/.local/share/pawmodoro` on Linux, `%APPDATA%\Pawmodoro` on Windows, instead of a literal `.local/share` folder showing up in a Windows user's home directory.
  - Added `install.ps1` / `install.bat` (Windows) alongside the existing `install.sh` (Linux) — same idea, adapted per-platform: private venv, no system-wide changes, a Desktop shortcut instead of a `.desktop` launcher entry.
  - Generated `resources/icon.ico` (Windows shortcuts need `.ico`, not `.png`).
  - `mpris.py`/`playerctl` (Linux-only media control) already degraded gracefully when missing — on Windows it just quietly reports unavailable, and Spotify (if connected) keeps working normally.

- **Optional Spotify Connect integration** (`spotify_client.py`): connect your Spotify account from the Music tab (OAuth, your own free Spotify Developer app — see "Connecting Spotify" below) and it becomes a selectable player in the bottom bar and widget mode, alongside any real MPRIS player. Spotify's real Web API genuinely supports volume/shuffle/repeat, so those controls work too when it's selected — unlike browser-based MPRIS control, which usually can't do those.
- **The window can be resized** — the pinned desktop-widget window had a hard-coded fixed width that blocked resizing; replaced with a minimum size and a drag handle (frameless windows have no OS resize border, so a `QSizeGrip` is added in the corner). Size is now remembered across sessions, same as position.
- **Delete/hide default ambient sounds**: every ambient sound row (built-in or custom) now has a ✕. Removing a built-in just hides it from your list (a "Restore removed default sounds" button appears whenever something's hidden) — nothing is deleted from disk.
- **Royal Purple theme added**: a bold, saturated purple alongside the existing muted Lavender Dusk and Deep Plum — six palettes total now.
- **Bring your own ambient sound files**: an "➕ Add your own sound…" button under the ambient list opens a file picker — pick any audio file, give it a name, and it joins the six built-ins as a regular checkbox + volume slider row (still counts toward the same 3-at-once limit). Custom sounds have a small ✕ button to remove them. Stored as a path reference (the file isn't copied), so don't move or delete the original file. See "Custom ambient sounds" below for format compatibility notes.
- **Bottom player bar now actually controls the in-app Music tab** (`music_tab.py`, `player_bar.py`, `widget_window.py`): an embedded `QWebEngineView` doesn't publish an MPRIS session the way a real browser does, so `playerctl` could never see it — that's why the transport buttons didn't do anything before. Instead, the player bar now drives the YouTube Music page directly via injected JavaScript (clicking its own play/pause/next/previous buttons), selectable as **"In-app Music tab"** in the player dropdown — and it's the default whenever the Music tab exists. Real MPRIS players (an external browser tab, etc.) still show up in the same dropdown if you use one instead.
- **Two more ambient sounds**: **Café Ambience** (murmur + occasional cup/spoon clinks) and **Fireplace** (low roar + irregular crackle/pop transients), joining Rain/Ocean/White Noise/Wind — six built-in now, still up to 3 at once (custom sounds included in that count).
- **YouTube Music is embedded in-app again** (`music_tab.py`): back to a QtWebEngine view with a persistent login profile, plus an "Open in browser…" button if you ever want to pop the current page out to your system browser instead. (`PyQt6-WebEngine` is back in `requirements.txt` as a result.)
- **Theme picker made hard to miss**: a "🎨 Theme" button now sits directly in the app's header, always visible regardless of window state — in addition to the View menu and the tray menu, which both still have it too. All three point at the same palettes and stay in sync.
- **Notes and Checklist merged** into one tab (`notes_checklist_tab.py`), side by side in a resizable splitter.
- **Color themes** (`theme.py`): six selectable palettes — warm Paper (default), Lavender Dusk, Deep Plum, Royal Purple, Charcoal Grey, and Slate & Mauve — switchable live, no restart needed. Your choice is remembered.
- **Notes are rich text** (`notes_toolbar.py`): bold, underline, five themed "ink" colors plus a custom color picker, two heading levels, and basic bullet/numbered lists. Saved as HTML so formatting persists; old plain-text notes still load fine.
- **Rain sounds rebuilt twice over**: first to use a system CLI audio player (`ffplay`/`paplay`/`pw-play`/`aplay`) instead of Qt's unreliable bundled multimedia backend, then to actually sound like rain — layered low/mid/high noise bands, an organic (non-mechanical) intensity drift, Poisson-distributed droplet transients, and a long equal-power-crossfaded loop so the seam is inaudible. When `ffplay` is available it loops natively in a single process — fully gapless.
- **App icon**: `resources/icon.png` (plus 256/128/64px variants), used for the window, taskbar, and tray.
- **Transport icons redrawn** (`player_icons.py`): the play/pause/prev/next buttons are drawn with QPainter instead of Unicode media-control glyphs, which had inconsistent font coverage and were showing as blank lines on some systems.
- **Installer hardening**: `install.sh` reliably kills any already-running instance before installing and does a clean file wipe, so updates can't silently fail to apply.

## Connecting Spotify

This is optional — the app works fine without it. Spotify Connect controls whatever device already has Spotify open and playing (desktop app, phone, web player) — it doesn't stream audio itself, so start playback there first.

Because Pawmodoro isn't a published/registered app, Spotify requires **you** to create your own free Developer app (takes about two minutes) rather than me shipping one:

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and log in with your Spotify account.
2. Click "Create app". Name/description can be anything.
3. Under **Redirect URIs**, add exactly: `http://127.0.0.1:8890/callback` — this has to match exactly, including the port.
4. Save, then copy the **Client ID** shown on the app's page (you don't need the Client Secret — the auth flow used here (PKCE) doesn't need one).
5. In Pawmodoro's Music tab, paste that Client ID into the field next to "Connect Spotify" and click it. Your browser opens to Spotify's sign-in/approval page; once you approve, it redirects back to a local page that says "Connected" and the app picks it up automatically.

Once connected, "Spotify" appears in the bottom player bar's dropdown (and in widget mode) alongside any real MPRIS player. Click "Disconnect" in the Music tab any time to revoke it locally.

**Honest testing note:** I don't have network access or a Spotify account in the environment I built this in, so I followed Spotify's documented OAuth/API contract carefully and tested everything I could without live credentials — the PKCE code generation, and the local callback listener (spun up a real one and confirmed it correctly captures an incoming redirect). The actual token exchange and playback API calls follow the spec but haven't been exercised against Spotify's live servers. If you hit an auth or API error, paste the message back and I'll fix it fast.

## Custom ambient sounds

Pomodoro tab → "Ambient sounds" panel → "➕ Add your own sound…". Pick any audio file, name it, and it's added to the list permanently (stored in your data file — the path + label — the file itself stays wherever it already was, nothing is copied).

**Format compatibility depends on which audio player Pawmodoro found on your system** (`ambient_loop.py` checks for `ffplay` first everywhere, then `paplay`/`pw-play`/`aplay` on Linux only):
- If **`ffplay`** is available (it's ffmpeg-based), pretty much any format works — mp3, ogg, flac, m4a, wav, etc. — on both Linux and Windows.
- On Linux without ffplay, falling back to `paplay`/`pw-play`/`aplay` — much more particular, **plain `.wav` is the safe bet**; compressed formats may just stay silent.
- On Windows without ffplay, falling back to PyQt6's own audio backend — also **plain `.wav` is the safe bet** there.

A row for a non-wav file (when ffplay isn't the active player) shows a tooltip warning about this rather than pretending it'll definitely work.

If a file goes missing (moved or deleted after adding it), its row shows disabled with a "File not found on disk" tooltip — click the ✕ to remove that entry.

## Controlling music from the player bar

The player bar's dropdown lists Spotify (once connected) plus any browser tab currently playing something — a YouTube Music tab, etc. There's no more "in-app" control path — that required embedding a full browser (QtWebEngine) in the app, which was removed for resource-usage reasons (see "What changed" above).

The mechanism differs per OS but the effect is the same — no extra setup, any browser that supports the web Media Session API (Chrome, Edge, Brave, Firefox) is picked up automatically once something's playing:
- **Linux**: via `playerctl`/MPRIS (`mpris.py`) — see "Playback controls: installing playerctl" below.
- **Windows**: via the same System Media Transport Controls API that powers the "Now Playing" flyout in the Windows volume/taskbar overlay (`smtc_windows.py`), no extra install needed. **Honest testing note**: written carefully against the documented WinRT API, but built without a live Windows machine to verify it against — if a browser tab isn't showing up or a control doesn't do anything, paste the exact behavior back and I'll fix it fast.

**Volume/shuffle/loop work fully for Spotify** (its own Web API genuinely supports them) but are **best-effort for a browser tab** — browsers only expose what the Media Session API supports. On Windows specifically, **volume control isn't available at all for a browser tab** (Windows' media-control API doesn't expose it) — the volume slider there only works with Spotify selected.

## Ambient sounds

Pomodoro tab → "Ambient sounds" panel. Six tracks, check up to 3 to play together, each with its own volume slider:
- **Rain** — layered noise bed + individually randomized droplet transients
- **Ocean Waves** — slow irregular wave-cycle envelope over low/mid noise
- **White Noise** — flat, gently smoothed noise for masking
- **Wind** — broad, slow gusts over low/mid noise, no droplets
- **Café Ambience** — murmuring mid-band noise with a slow conversational swell, plus sparse cup/spoon clink transients
- **Fireplace** — low-frequency roar with a gentle flicker envelope, plus sharp, irregular crackle/pop transients

All six are synthesized locally (see `tools/generate_ambient.py` and `tools/generate_rain.py`), not sampled audio, and loop seamlessly. Want a different built-in sound added (e.g. a train car, brown noise, birdsong)? Tell me and I'll generate one the same way — or add your own file directly, see "Custom ambient sounds" below.

## Color themes

**View → Color theme** in the menu bar, the **🎨 Theme** button in the app's header (always visible, doesn't depend on menu-bar rendering), or right-click the tray icon — all three show the same six palettes (Paper, Lavender Dusk, Deep Plum, Royal Purple, Charcoal Grey, Slate & Mauve) and stay in sync. The app remembers your choice and reapplies it on next launch. Want a specific custom palette (exact colors) instead of picking from the six? Just tell me the hex codes or describe the vibe and I'll add it as a preset in `theme.py`.

## Playback controls: installing `playerctl` (Linux only)

This section doesn't apply on Windows — `playerctl`/MPRIS is a Linux thing, and it's not needed anyway since Spotify (if connected) is always available as a player option there.

The player bar needs `playerctl` on your system PATH. It's **not available via Homebrew**. On Bazzite:

```bash
# Option A: layer it onto the OS image (simple, needs a reboot)
rpm-ostree install playerctl

# Option B: no reboot, using distrobox (comes preinstalled on Bazzite)
distrobox create -n tools -i fedora:latest
distrobox enter tools -- sudo dnf install -y playerctl
distrobox enter tools -- distrobox-export --bin /usr/bin/playerctl
```

If `rpm-ostree install` fails with a "min-free-space-percent" error, your OSTree partition is low on space — run `rpm-ostree cleanup -bpr` to reclaim space from old deployments, or just use the distrobox route instead, which doesn't touch the OS image at all.

**Note on `distrobox-export`:** it drops the wrapper in `~/.local/bin/playerctl`, but apps launched from the KDE app menu (rather than a terminal) don't always inherit `~/.local/bin` on their `PATH`. Pawmodoro checks that location directly regardless of `PATH`, and prints where it looked (`[mpris] playerctl found at ...` / `NOT found`) if you run it from a terminal — useful for double-checking.

**Volume and shuffle controls are best-effort.** Browsers only forward what the web Media Session API supports to MPRIS — play/pause/next/previous/seek work reliably for a YouTube Music tab, but volume and shuffle aren't part of that API, so those two controls may only do something with native MPRIS apps (e.g. a Spotify desktop client), not a browser tab. They're included since they're genuinely useful when you do have a native player active.

## Why this approach

- Plain Python + PyQt6, installed into a **private virtualenv** — no system package changes on either OS. On Bazzite (an immutable, image-based distro) this specifically avoids `rpm-ostree` layering; on Windows it avoids touching any system-wide Python install.
- The "desktop widget" is a frameless always-on-top Qt window (the same mechanism KDE's own "Keep above others" + "Skip taskbar" window rules use, and the equivalent of an always-on-top gadget on Windows), not a full Plasma QML plasmoid or a native Windows widget. That means it installs and runs instantly on both, at the cost of not being drag-and-drop-able onto an actual desktop widget layer. Happy to build a real Plasma plasmoid as a Linux-specific follow-up if you want it.
- YouTube Music opens in your regular system browser (no embedded browser anymore — see "What changed" above for why), and the player bar controls it there via MPRIS on Linux, or you can use Spotify Connect instead on either OS.
- Ambient sound playback shells out to small, ubiquitous system CLI tools (`ffplay` first choice on both platforms) rather than depending on Qt's bundled multimedia backend, which is the part most likely to be missing or misconfigured — specifically on Linux; Windows falls back to that same Qt backend since it's normally solid there.

## Installing on Linux (Bazzite / other distros)

If you've already installed an older version, just re-run the installer from this updated folder — it overwrites the previous copy and refreshes the venv:

```bash
cd pawmodoro
chmod +x install.sh
./install.sh
```

This will:
- Create/update `~/.local/share/pawmodoro-app/` with a private Python venv
- Copy over the `resources/` folder (icon + ambient sound files)
- Install dependencies into that venv only
- Add/refresh the launcher entry so "Pawmodoro" shows up in your KDE application launcher, with its own icon
- Print an optional command to make it autostart on login

### Run manually (Linux)

```bash
~/.local/share/pawmodoro-app/run.sh
```

or, from source without installing:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

## Using it

- **Notes & Checklist tab**: notes on the left (ruled paper look, with a formatting toolbar), checklist on the right. Type a task, pick "daily" for repeating chores, click Add.
- **Pomodoro tab**: set your minutes/session counts, click "Save settings", then Start. Check up to 3 ambient sounds underneath for focus noise, each with its own volume slider — remove any with ✕, or add your own file.
- **Music tab**: click "Open YouTube Music" to launch it in your regular browser (already logged in there). Optionally connect Spotify here too (see "Connecting Spotify" above).
- **Bottom player bar**: always visible, controls whichever player you pick from its dropdown — Spotify (once connected), or any real MPRIS player (Linux only).
- **Pin as desktop widget** (View menu, tray icon menu, or the 🎨 Theme button's neighboring menu action): shrinks to a small draggable, **resizable** panel (drag the corner handle) with your pending tasks, the timer, and mini playback controls. Click the restore arrow (⤡) to bring the full window back, or click the tray icon.
- Closing the main window doesn't quit the app — it minimizes to the system tray. Right-click the tray icon → Quit to fully exit.

## Uninstall

**Linux:**
```bash
rm -rf ~/.local/share/pawmodoro-app ~/.local/share/pawmodoro
rm -f ~/.local/share/applications/pawmodoro.desktop ~/.config/autostart/pawmodoro.desktop
```

**Windows:** delete `%LOCALAPPDATA%\Pawmodoro` and `%APPDATA%\Pawmodoro`, and remove the `Pawmodoro` shortcut from your Desktop.

## Known limitations / honest notes

- Written and carefully reviewed, but **not run against a live display on either OS** — no GUI, sound card, or Windows machine in the environment I built it in. What I could and did actually execute and verify: the storage/data logic (including custom-sound, hidden-sound, and Spotify-config round-trips), cross-platform path resolution (`paths.py`, tested against simulated Windows/Linux), the theme palette-switching logic (each of the six palettes confirmed to produce distinct, correct CSS), the ambient-sound threading mechanism (spawn/confirm-alive/respawn-on-exit/stop, tested with a fake player to verify 3 concurrent tracks work correctly, independent of this sandbox's lack of real audio hardware), Spotify's PKCE code generation (RFC 7636-compliant) and local OAuth callback listener (spun up a real one, sent it a simulated redirect, confirmed it correctly captured the code), and the icon/ambient audio file generation (including the new `.ico`). The Qt widgets themselves weren't visually tested on either platform, and `install.ps1`/`install.bat` haven't been run on a real Windows machine. Do a quick smoke test after install on either OS; paste back any traceback or error and I'll fix it fast.
- Spotify's actual token exchange and playback API calls follow the documented spec but haven't been exercised against Spotify's live servers (no network/account in my build environment) — see "Connecting Spotify" above.
- Ambient sound playback needs one of `ffplay`, `paplay`, `pw-play`, or `aplay` on Linux (Bazzite should have `paplay` at minimum), or `ffplay` on Windows (falls back to a Qt-based backend if not found). If a row is greyed out, check the tooltip.
- Ruled notebook lines are drawn at a fixed spacing, so headings (which use a larger font) won't line up perfectly with the lines behind them — a cosmetic quirk of the ruled-paper effect, not a bug.
- "Widget mode" is a pinned mini-window, not a real desktop plasmoid/gadget.
- The player bar/widget mode lets you pick between Spotify (if connected) and any real MPRIS player (Linux only — this option just won't appear on Windows). Volume/shuffle work fully for Spotify, and are best-effort for MPRIS players.
- Spotify calls made from a button click (play/pause/next/etc.) are a brief blocking network round-trip; the periodic "now playing" polling is threaded off the UI thread specifically to avoid stutter, but a single click could have a short (usually sub-second, capped at 8s) delay on a slow connection.

## Troubleshooting: "I reinstalled but nothing changed"

Pawmodoro deliberately keeps running in the system tray when you close its window, so it's always one click away. The downside: if you reinstall while an old copy is still running in the tray, that **old process keeps running the old code** even though the files on disk are updated — so nothing appears to change until you restart it. Both `install.sh` and `install.ps1`/`install.bat` detect and stop a running instance automatically before installing.

`install.sh` now detects and stops any running instance automatically before installing. If you're still not seeing an update after running it:

1. Right-click the tray icon and choose **Quit** explicitly, just in case.
2. Confirm you're running the installer from a freshly extracted copy of the *new* zip, not an old folder.
3. Relaunch and check the window title — it should read the current version (e.g. `Pawmodoro v1.3.0`). If it still shows an old version, something's still holding onto the old process; run `pkill -f pawmodoro` in a terminal, then launch again.
