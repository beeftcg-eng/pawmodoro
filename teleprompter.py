"""
teleprompter.py - The teleprompter's arithmetic (no Qt, no storage): word
counts, how long a script takes to read, and how fast to scroll so the text
moves past the reading line at a chosen words-per-minute pace.

Speed is set in words per minute rather than pixels, so it means the same
thing at any font size or window width, and the tab's "about 3:20 at 150
wpm" estimate is what the prompter actually takes.
"""
import html
import re

WPM_MIN = 60
WPM_MAX = 300
WPM_STEP = 10
FONT_MIN = 16
FONT_MAX = 160
FONT_STEP = 4
MARGIN_MAX = 40  # percent of the width left empty on each side
COUNTDOWN_MAX = 10

DEFAULT_SETTINGS = {
    "wpm": 150,  # a relaxed speaking pace
    "font_size": 56,
    "mirror": False,  # flipped left-right, for a beam-splitter (glass) teleprompter
    "margin": 12,
    "countdown": 3,  # seconds of 3-2-1 before it starts scrolling
    "fullscreen": True,
    "screen": None,  # name of the monitor to open on (None: the one the main window is on)
}


# [smile], [show the product]: stage directions, shown in a different color and not read out.
CUE_RE = re.compile(r"\[[^\[\]\n]*\]")


def spoken_text(text):
    return CUE_RE.sub(" ", text or "")


def word_count(text):
    """Words to be read out (cues in [brackets] don't count)."""
    return len(re.findall(r"\S+", spoken_text(text)))


def script_html(text, cue_color):
    """The script as rich text for the prompter: a paragraph per line, cues in `cue_color`."""
    paragraphs = []
    for line in (text or "").splitlines() or [""]:
        escaped = html.escape(line)
        escaped = CUE_RE.sub(lambda m: f'<span style="color:{cue_color}; font-style:italic;">{m.group(0)}</span>',
                             escaped)
        paragraphs.append(f"<p>{escaped or '&nbsp;'}</p>")
    return "".join(paragraphs)


def read_seconds(text, wpm):
    words = word_count(text)
    return words * 60.0 / clamp_wpm(wpm) if words else 0.0


def format_duration(seconds):
    seconds = int(round(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def scroll_speed(wpm, text_height, words):
    """Pixels per second that move `words` words laid out `text_height`
    pixels tall past the reading line at `wpm`."""
    if words <= 0 or text_height <= 0:
        return 0.0
    return clamp_wpm(wpm) / 60.0 * text_height / words


def clamp_wpm(wpm):
    return max(WPM_MIN, min(WPM_MAX, int(wpm)))


def clamp_font(size):
    return max(FONT_MIN, min(FONT_MAX, int(size)))


def clean_settings(settings):
    """`settings` with every value in range and missing ones defaulted."""
    out = dict(DEFAULT_SETTINGS)
    out.update({k: v for k, v in (settings or {}).items() if k in DEFAULT_SETTINGS})
    out["wpm"] = clamp_wpm(out["wpm"])
    out["font_size"] = clamp_font(out["font_size"])
    out["margin"] = max(0, min(MARGIN_MAX, int(out["margin"])))
    out["countdown"] = max(0, min(COUNTDOWN_MAX, int(out["countdown"])))
    out["mirror"] = bool(out["mirror"])
    out["fullscreen"] = bool(out["fullscreen"])
    return out
