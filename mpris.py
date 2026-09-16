"""
mpris.py - Thin, defensive wrapper around the `playerctl` CLI (standard
Linux MPRIS control) shared by the main player bar and the compact widget
window, so both stay in sync without duplicating subprocess logic.
"""
import os
import shutil
import subprocess

# GUI apps launched from a desktop/app-menu entry often DON'T inherit the
# same PATH a terminal shell has (e.g. ~/.local/bin, where tools like
# `distrobox-export` install wrapper scripts, may be missing). So on top of
# a normal PATH lookup, also check the common install spots directly.
_CANDIDATE_PATHS = [
    os.path.expanduser("~/.local/bin/playerctl"),
    "/usr/local/bin/playerctl",
    "/usr/bin/playerctl",
    "/var/lib/flatpak/exports/bin/playerctl",
]


def _locate_playerctl():
    found = shutil.which("playerctl")
    if found:
        return found
    for path in _CANDIDATE_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


PLAYERCTL = _locate_playerctl()
print(f"[mpris] playerctl {'found at ' + PLAYERCTL if PLAYERCTL else 'NOT found (checked PATH + ' + ', '.join(_CANDIDATE_PATHS) + ')'}")


def available():
    return PLAYERCTL is not None


def run(args, timeout=1.5):
    """Best-effort subprocess call to playerctl. Never raises."""
    if not PLAYERCTL:
        return ""
    try:
        result = subprocess.run(
            [PLAYERCTL] + args, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def list_players():
    return [p for p in run(["-l"]).splitlines() if p.strip()]


def status(player):
    return run(["-p", player, "status"])


_METADATA_SEP = "\x1f"  # unit separator: won't collide with real title/artist text


def now_playing_bundle(player):
    """Title, artist and track length in one playerctl call instead of
    three separate subprocess spawns (title, artist, mpris:length were
    previously queried independently by every poll tick). Returns
    (display_text, length_seconds_or_None)."""
    raw = run(["-p", player, "metadata", "--format",
               f"{{{{ title }}}}{_METADATA_SEP}{{{{ artist }}}}{_METADATA_SEP}{{{{ mpris:length }}}}"])
    title, _, rest = raw.partition(_METADATA_SEP)
    artist, _, length_raw = rest.partition(_METADATA_SEP)
    try:
        length = float(length_raw) / 1_000_000  # microseconds -> seconds
    except ValueError:
        length = None
    text = (f"{title} \u2014 {artist}" if title and artist else title) if title else ""
    return text, length


def command(player, action):
    """action: 'previous' | 'play-pause' | 'next'"""
    if player:
        run(["-p", player, action])


def seek(player, seconds):
    """Relative seek. Positive = forward, negative = backward."""
    if not player:
        return
    sign = "+" if seconds >= 0 else "-"
    run(["-p", player, "position", f"{abs(seconds)}{sign}"])


def get_volume(player):
    """Returns 0.0-1.0, or None if unsupported/unavailable."""
    v = run(["-p", player, "volume"])
    try:
        return float(v)
    except ValueError:
        return None


def set_volume(player, level):
    """level: 0.0-1.0. Not all players (notably some browser MPRIS bridges)
    support this — it's a best-effort call."""
    if player:
        run(["-p", player, "volume", f"{level:.2f}"])


def get_position_seconds(player):
    v = run(["-p", player, "position"])
    try:
        return float(v)
    except ValueError:
        return None


def get_loop_status(player):
    return run(["-p", player, "loop"])  # "None" | "Track" | "Playlist"


def set_loop_status(player, value):
    if player:
        run(["-p", player, "loop", value])


def get_shuffle(player):
    return run(["-p", player, "shuffle"])  # "On" | "Off"


def set_shuffle(player, value):
    if player:
        run(["-p", player, "shuffle", value])


def format_seconds(total):
    if total is None:
        return "--:--"
    total = max(0, int(total))
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def get_url(player):
    """The URL of what's currently loaded, when the player exposes one
    (browsers do, for the page/media source). Used to tell which MPRIS
    entry is actually the YouTube Music tab when several browser players
    are present at once (e.g. a regular YouTube video tab too)."""
    return run(["-p", player, "metadata", "xesam:url"])


def pick_default(players):
    if not players:
        return None

    # Prefer whichever player's currently-loaded page is YouTube Music
    # specifically — otherwise, with multiple browser tabs playing media,
    # the "first browser-looking name" heuristic below could just as
    # easily land on an unrelated YouTube video tab.
    for p in players:
        if "music.youtube.com" in get_url(p):
            return p

    preferred = next(
        (p for p in players if any(
            b in p.lower() for b in ("firefox", "chrom", "brave", "edge", "vivaldi")
        )),
        players[0],
    )
    return preferred
