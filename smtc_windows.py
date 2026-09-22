"""
smtc_windows.py - Windows equivalent of mpris.py. This is what lets the
player bar control a real browser tab (YouTube Music, etc.) on Windows,
since MPRIS/playerctl (mpris.py) is Linux-only and simply doesn't exist
on Windows.

Uses the `winsdk` package (WinRT bindings for Python) to talk to
Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager -
the same API that powers the "Now Playing" flyout in the Windows
volume/taskbar overlay. Every browser that implements the web Media
Session API (Chrome, Edge, Brave, Firefox) publishes a session here
automatically once something's playing - no extra setup needed, same
deal as MPRIS on Linux.

Exposes the same function names/shapes as mpris.py so player_bar.py and
widget_window.py don't need to know which backend they're talking to -
see those files for the platform.system() == "Windows" import swap.

Honest limitation: written carefully against the documented WinRT API
surface, but built on Linux with no live Windows machine to verify it
against. Every call is defensive (catches broadly, degrades to an empty/
None result rather than raising) specifically because of that - if
something about the real API shape is off, the app should just show
"not controllable" rather than crash. If something doesn't work, paste
the exact behavior/error back.

Caveats versus real MPRIS:
- No volume control at all - SMTC doesn't expose per-session volume.
- No "which tab is actually YouTube Music" check the way Linux's
  mpris.py can (it inspects xesam:url) - SMTC only exposes title/artist/
  album, not the page URL, so with several browser tabs playing media at
  once, picking a default falls back to a browser-name heuristic only.
"""
import asyncio
import os
import traceback

# Set PAWMODORO_SMTC_DEBUG=1 to get every error (not just the first of each
# kind) plus a full traceback, and a session count on every list_players()
# call - useful for a user to paste back when this module isn't finding a
# browser session and the reason isn't obvious from the plain output above.
_SMTC_DEBUG = os.environ.get("PAWMODORO_SMTC_DEBUG") == "1"

try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
    )
    _WINSDK_OK = True
    print("[smtc] winsdk loaded OK — browser tab control available")
except ImportError as e:
    _WINSDK_OK = False
    print(f"[smtc] winsdk not available ({e}) — only Spotify Connect will show up as a player. "
          "Run install.bat's console version (run_console.bat) to see this line; if it says "
          "something other than a plain \"module not found\", paste it back.")

_BROWSER_HINTS = ("firefox", "chrome", "msedge", "edge", "brave", "vivaldi", "opera")

# GlobalSystemMediaTransportControlsSessionPlaybackStatus values (documented,
# stable across WinRT versions): Closed=0, Opened=1, Changing=2, Stopped=3,
# Playing=4, Paused=5. Compared as plain ints below rather than importing the
# enum class, since its exact Python attribute naming isn't something I can
# verify from here.
_PLAYING, _PAUSED = 4, 5


def available():
    return _WINSDK_OK


_last_error_logged = None


def _run(fn):
    """Every entry point here is a synchronous, one-shot call (mirrors
    mpris.py's subprocess-per-call shape), so just spin up a fresh event
    loop each time rather than keeping one alive across the whole app.

    `fn` is a zero-arg callable that *returns* the awaitable, not the
    awaitable itself - constructing it (e.g. evaluating
    `session.try_toggle_play_pause_async()`) can itself raise (wrong
    attribute name on this winsdk version, wrong arg count), and building
    it inside this try is what funnels that through the one logged path
    below instead of vanishing into a bare `except: pass` at the call
    site, which would look identical to "nothing to control"."""
    global _last_error_logged
    if not _WINSDK_OK:
        return None
    try:
        return asyncio.run(fn())
    except Exception as e:
        # Only the *first* occurrence of each distinct error is printed -
        # this runs on every player_bar poll tick (every ~1.5s), and a
        # real bug would otherwise flood run_console.bat's output. This
        # is specifically what makes a genuine implementation bug
        # distinguishable from "no browser session right now" instead of
        # both looking identical from the outside.
        message = f"{type(e).__name__}: {e}"
        if _SMTC_DEBUG or message != _last_error_logged:
            _last_error_logged = message
            print(f"[smtc] unexpected error talking to Windows media session API: {message}")
            if _SMTC_DEBUG:
                traceback.print_exc()
        return None


async def _get_sessions():
    manager = await _Manager.request_async()
    return list(manager.get_sessions())


def _find_session(player_id):
    sessions = _run(_get_sessions) or []
    for s in sessions:
        try:
            if s.source_app_user_model_id == player_id:
                return s
        except Exception:
            continue
    return None


def _timespan_seconds(ts):
    """Windows.Foundation.TimeSpan - defensive about the exact attribute
    winsdk's Python projection uses (.total_seconds() if it behaves like a
    Python timedelta, else .duration in 100ns ticks, the raw WinRT field)."""
    if ts is None:
        return None
    total = getattr(ts, "total_seconds", None)
    if callable(total):
        try:
            return total()
        except Exception:
            pass
    duration = getattr(ts, "duration", None)
    if duration is not None:
        try:
            return duration / 10_000_000
        except Exception:
            pass
    return None


def list_players():
    if not _WINSDK_OK:
        return []
    sessions = _run(_get_sessions) or []
    if _SMTC_DEBUG:
        print(f"[smtc] list_players: {len(sessions)} session(s)")
    players = []
    for s in sessions:
        try:
            players.append(s.source_app_user_model_id)
        except Exception:
            continue
    return players


def pick_default(players):
    if not players:
        return None
    return next(
        (p for p in players if any(b in p.lower() for b in _BROWSER_HINTS)),
        players[0],
    )


def status(player):
    session = _find_session(player)
    if not session:
        return ""
    try:
        playback_status = int(session.get_playback_info().playback_status)
    except Exception:
        return ""
    if playback_status == _PLAYING:
        return "Playing"
    if playback_status == _PAUSED:
        return "Paused"
    return "Stopped"


def now_playing_bundle(player):
    session = _find_session(player)
    if not session:
        return "", None
    props = _run(session.try_get_media_properties_async)
    title = (getattr(props, "title", "") or "") if props else ""
    artist = (getattr(props, "artist", "") or "") if props else ""
    text = (f"{title} — {artist}" if title and artist else title) if title else ""
    length = None
    try:
        length = _timespan_seconds(session.get_timeline_properties().end_time)
    except Exception:
        pass
    return text, length


def command(player, action):
    session = _find_session(player)
    if not session:
        return
    if action == "play-pause":
        _run(session.try_toggle_play_pause_async)
    elif action == "next":
        _run(session.try_skip_next_async)
    elif action == "previous":
        _run(session.try_skip_previous_async)


def seek(player, seconds):
    pass  # Not used anywhere in the app currently.


def get_position_seconds(player):
    session = _find_session(player)
    if not session:
        return None
    try:
        return _timespan_seconds(session.get_timeline_properties().position)
    except Exception:
        return None


def get_volume(player):
    return None  # SMTC doesn't expose per-session volume at all.


def set_volume(player, level):
    pass  # Not supported by SMTC.


def get_loop_status(player):
    session = _find_session(player)
    if not session:
        return ""
    try:
        mode = int(session.get_playback_info().auto_repeat_mode)
    except Exception:
        return ""
    return {0: "None", 1: "Track", 2: "Playlist"}.get(mode, "None")


def set_loop_status(player, value):
    session = _find_session(player)
    if not session:
        return
    mode = {"None": 0, "Track": 1, "Playlist": 2}.get(value, 0)
    _run(lambda: session.try_change_auto_repeat_mode_async(mode))


def get_shuffle(player):
    session = _find_session(player)
    if not session:
        return ""
    try:
        active = session.get_playback_info().is_shuffle_active
    except Exception:
        return ""
    if active is None:
        return ""
    return "On" if active else "Off"


def set_shuffle(player, value):
    session = _find_session(player)
    if not session:
        return
    _run(lambda: session.try_change_shuffle_active_async(value == "On"))


def get_url(player):
    return ""  # SMTC doesn't expose the page URL, only title/artist/album.


def format_seconds(total):
    if total is None:
        return "--:--"
    total = max(0, int(total))
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"
