# Windows test pass

GitHub's Windows runner already checks, on every push (`.github/workflows/tests.yml`):
the Python tests, a real `install.ps1` install (files, Desktop shortcut, PyQt6),
and that the installed app starts and stays up. What it can't do is anything that
needs a real desktop session: a tray, notifications, a browser playing music, a
mouse. This list covers those. It takes about 15 minutes. Note anything that
doesn't match, plus the output of `%LOCALAPPDATA%\Pawmodoro\run_console.bat`
if something fails.

## Install

- [ ] Download `Pawmodoro-Setup.exe` from the latest release and run it. It should finish without asking for admin rights.
- [ ] A **Pawmodoro** shortcut appears on the Desktop and in the Start Menu, and starts the app with no console window.
- [ ] Start Menu → right-click Pawmodoro → **Pin to taskbar**. Close and reopen the app from the taskbar pin: one taskbar icon, not two.
- [ ] Settings → Apps → Installed apps lists Pawmodoro, and **Uninstall** removes it (your notes in `%APPDATA%\Pawmodoro` stay).

## Tray and notifications

- [ ] Closing the window leaves Pawmodoro in the tray (bottom right, maybe under ^), with a "Still running" notification.
- [ ] Right-click the tray icon: **Start timer** starts the pomodoro; the menu then says **Pause timer (24:59 left)**. Hovering the icon shows the time left.
- [ ] Give a checklist task a reminder a minute ahead, close the window, wait. A notification appears; clicking it opens Pawmodoro on that task with the Done / Snooze banner.
- [ ] Clicking a second Pawmodoro shortcut while it's running brings the existing window forward instead of opening another.

## Music controls

- [ ] Play something on music.youtube.com in Edge or Chrome. Within a few seconds the player bar shows the track; play/pause and next work.
- [ ] Same with the window closed to the tray and then reopened: the track shows up again within a couple of seconds.

## Timer

- [ ] Start a work session, leave the mouse and keyboard alone for 6 minutes (the default pause is after 5). The timer pauses with a "Paused: no keyboard or mouse for 5 min" banner and a notification, and the time away is added back. **I was here — count it** resumes with that time counted.

## Keyboard and menus

- [ ] Ctrl+1 to Ctrl+5 switch tabs; Ctrl+P starts/pauses the timer; Ctrl+T jumps to a new checklist task; Ctrl+N asks for a new notes page.
- [ ] View → **Export everything…** saves a zip; it contains `notes/`, `checklist.md` and `pawmodoro-data.json`, and text you made **bold** in your notes shows as `**bold**` in the exported `.md` file.
- [ ] View → **Restore from a backup…** → pick today's → Pawmodoro restarts by itself (a single window, a single tray icon).

## Updating

- [ ] With an older version installed, the **⬆ Update** button appears; **Update now** installs and restarts into the new version.
