"""
theme.py - A "pen and paper" visual theme applied app-wide, with several
selectable color palettes (warm paper, plus purples and greys). Pure QSS
+ QFont, no image assets required.

Any widget that uses inline stylesheets (rather than the global QSS) reads
colors from `current()` at build time, and exposes a `refresh_theme()`
method so `main.py` can restyle it live when the palette changes, without
needing a restart.
"""
from PyQt6.QtGui import QFont

SERIF_FALLBACKS = ["Noto Serif", "DejaVu Serif", "Liberation Serif", "Georgia", "serif"]
MONO_FALLBACKS = ["JetBrains Mono", "Fira Code", "Cascadia Code", "DejaVu Sans Mono", "Liberation Mono", "Consolas", "monospace"]

# Fonts to draw emoji with, tried after the text font and before the generic family. On Linux, Qt's own
# fallback picks "Noto Color Emoji", and where that is the newer COLRv1 build (Fedora 44, for one) Qt can't draw it,
# so 🏆 🏠 ☁️ and friends came out as blank gaps in tabs and buttons. Twemoji (COLRv0) draws everywhere Qt does, so it is
# tried first. A family that isn't installed (Windows, macOS: they have their own emoji fonts that Qt already
# finds) is skipped, so this changes nothing there.
EMOJI_FALLBACKS = ["Twemoji"]


def _with_emoji(stack):
    """`stack` with the emoji fonts slipped in before its final generic family ("serif" / "monospace")."""
    return stack[:-1] + EMOJI_FALLBACKS + stack[-1:]

# Each palette: PAPER (main bg), PAPER_LIGHT (card/input bg), PAPER_EDGE
# (borders), INK (main text), INK_SOFT (secondary text), ACCENT (highlight),
# ACCENT_SOFT (hover/selection tint), FONT ("serif" or "mono" - picks which
# fallback stack serif_font()/build_stylesheet() use).
PALETTES = {
    "Paper": {
        "PAPER": "#f4ecd8", "PAPER_LIGHT": "#fdf6e8", "PAPER_EDGE": "#c9b896",
        "INK": "#3a2f22", "INK_SOFT": "#6b5c46", "ACCENT": "#96433a", "ACCENT_SOFT": "#b98a83",
        "FONT": "serif",
    },
    "Lavender Dusk": {
        "PAPER": "#ece5f3", "PAPER_LIGHT": "#f7f3fa", "PAPER_EDGE": "#c1aad9",
        "INK": "#392f4d", "INK_SOFT": "#6d6086", "ACCENT": "#7d5ba6", "ACCENT_SOFT": "#b39cd0",
        "FONT": "serif",
    },
    "Deep Plum": {
        "PAPER": "#e5dde6", "PAPER_LIGHT": "#f3edf4", "PAPER_EDGE": "#a98bb0",
        "INK": "#2e2032", "INK_SOFT": "#63506a", "ACCENT": "#5c3566", "ACCENT_SOFT": "#8f6b98",
        "FONT": "serif",
    },
    "Royal Purple": {
        "PAPER": "#ece0f7", "PAPER_LIGHT": "#f8f1fc", "PAPER_EDGE": "#b98fd9",
        "INK": "#2b1240", "INK_SOFT": "#6b4a8a", "ACCENT": "#8e24aa", "ACCENT_SOFT": "#ba68c8",
        "FONT": "serif",
    },
    "Charcoal Grey": {
        "PAPER": "#e6e7ea", "PAPER_LIGHT": "#f4f5f6", "PAPER_EDGE": "#a9b0b8",
        "INK": "#282b30", "INK_SOFT": "#585e66", "ACCENT": "#4d5a6b", "ACCENT_SOFT": "#8994a1",
        "FONT": "serif",
    },
    "Slate & Mauve": {
        "PAPER": "#e8e4e8", "PAPER_LIGHT": "#f5f2f5", "PAPER_EDGE": "#ad9fac",
        "INK": "#2f2a30", "INK_SOFT": "#655c66", "ACCENT": "#6b4c63", "ACCENT_SOFT": "#a3899e",
        "FONT": "serif",
    },
    # --- Gamer themes: dark backgrounds, monospace, neon accents ---
    "Hacker Terminal": {
        "PAPER": "#080c08", "PAPER_LIGHT": "#0f150f", "PAPER_EDGE": "#1f3d1f",
        "INK": "#39ff14", "INK_SOFT": "#2ea82e", "ACCENT": "#00ff9c", "ACCENT_SOFT": "#0d8a2e",
        "FONT": "mono",
    },
    "Arcade Neon": {
        "PAPER": "#0d0221", "PAPER_LIGHT": "#1a0933", "PAPER_EDGE": "#4b1c73",
        "INK": "#00f0ff", "INK_SOFT": "#8a5cf6", "ACCENT": "#ff2fd6", "ACCENT_SOFT": "#b34fd1",
        "FONT": "mono",
    },
}

DEFAULT_PALETTE_NAME = "Paper"
_current_name = DEFAULT_PALETTE_NAME


def palette_names():
    return list(PALETTES.keys())


def current():
    return PALETTES.get(_current_name, PALETTES[DEFAULT_PALETTE_NAME])


def current_name():
    return _current_name


def set_current(name):
    global _current_name
    if name in PALETTES:
        _current_name = name


def font_family_stack():
    """The font fallback list for the current theme (mono for the gamer
    themes, serif for the pen-and-paper ones)."""
    return _with_emoji(MONO_FALLBACKS if current().get("FONT") == "mono" else SERIF_FALLBACKS)


def _css_font_family(font_key):
    stack = _with_emoji(MONO_FALLBACKS if font_key == "mono" else SERIF_FALLBACKS)
    return ", ".join(f if f in ("serif", "monospace") else f'"{f}"' for f in stack)


def serif_font(point_size=10, bold=False):
    font = QFont()
    font.setFamilies(font_family_stack())
    font.setPointSize(point_size)
    font.setBold(bold)
    return font


def build_stylesheet(name=None):
    p = PALETTES[name] if name in PALETTES else current()
    PAPER, PAPER_LIGHT, PAPER_EDGE = p["PAPER"], p["PAPER_LIGHT"], p["PAPER_EDGE"]
    INK, INK_SOFT, ACCENT, ACCENT_SOFT = p["INK"], p["INK_SOFT"], p["ACCENT"], p["ACCENT_SOFT"]
    FONT_FAMILY = _css_font_family(p.get("FONT", "serif"))

    return f"""
QWidget {{
    background-color: {PAPER};
    color: {INK};
    font-family: {FONT_FAMILY};
}}

QMainWindow, QDialog {{
    background-color: {PAPER};
}}

QMenuBar {{
    background-color: {PAPER};
    color: {INK};
    border-bottom: 1px solid {PAPER_EDGE};
}}
QMenuBar::item:selected {{
    background-color: {PAPER_EDGE};
    border-radius: 4px;
}}
QMenu {{
    background-color: {PAPER_LIGHT};
    border: 1px solid {PAPER_EDGE};
    color: {INK};
}}
QMenu::item:selected {{
    background-color: {ACCENT_SOFT};
    color: {PAPER_LIGHT};
}}

QTabWidget::pane {{
    border: 1px solid {PAPER_EDGE};
    border-radius: 8px;
    background-color: {PAPER_LIGHT};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {PAPER_EDGE};
    color: {INK};
    padding: 8px 18px;
    margin-right: 3px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{
    background-color: {PAPER_LIGHT};
    color: {ACCENT};
    font-weight: bold;
}}
QTabBar::tab:hover {{
    background-color: {ACCENT_SOFT};
    color: {PAPER_LIGHT};
}}

QPushButton, QToolButton {{
    background-color: {PAPER_EDGE};
    color: {INK};
    border: 1px solid {INK_SOFT};
    border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {ACCENT_SOFT};
    color: {PAPER_LIGHT};
}}
QPushButton:pressed, QToolButton:pressed {{
    background-color: {ACCENT};
    color: {PAPER_LIGHT};
}}
QPushButton:checked, QToolButton:checked {{
    background-color: {ACCENT};
    color: {PAPER_LIGHT};
}}

/* The pomodoro tab's main Start/Pause action — a filled pill button that
   stands out from the rest of the (otherwise fairly flat) button style. */
QPushButton#primary_btn {{
    background-color: {ACCENT};
    color: {PAPER_LIGHT};
    border: none;
    border-radius: 20px;
    padding: 10px 30px;
    font-size: 13px;
    font-weight: bold;
}}
QPushButton#primary_btn:hover {{
    background-color: {ACCENT_SOFT};
}}
QPushButton#primary_btn:pressed {{
    background-color: {INK_SOFT};
}}

/* Reset/Skip next to it: quieter, outline-only, so the eye lands on Start */
QPushButton#secondary_btn {{
    background-color: transparent;
    color: {INK_SOFT};
    border: 1px solid {PAPER_EDGE};
    border-radius: 16px;
    padding: 8px 16px;
}}
QPushButton#secondary_btn:hover {{
    background-color: {PAPER_EDGE};
    color: {INK};
}}
QPushButton#secondary_btn:pressed {{
    background-color: {ACCENT_SOFT};
    color: {PAPER_LIGHT};
}}

QLineEdit, QComboBox, QSpinBox {{
    background-color: {PAPER_LIGHT};
    border: 1px solid {PAPER_EDGE};
    border-radius: 4px;
    padding: 4px 6px;
    color: {INK};
}}
QComboBox QAbstractItemView {{
    background-color: {PAPER_LIGHT};
    color: {INK};
    selection-background-color: {ACCENT_SOFT};
}}

QListWidget {{
    background-color: {PAPER_LIGHT};
    border: 1px solid {PAPER_EDGE};
    border-radius: 6px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 6px 4px;
    border-bottom: 1px solid {PAPER_EDGE};
}}
QListWidget::item:selected {{
    background-color: {ACCENT_SOFT};
    color: {PAPER_LIGHT};
    border-radius: 4px;
}}

QGroupBox {{
    border: 1px dashed {PAPER_EDGE};
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 14px;
    font-weight: bold;
    color: {ACCENT};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}

QLabel {{
    color: {INK};
}}

QScrollBar:vertical {{
    background: {PAPER};
    width: 12px;
}}
QScrollBar::handle:vertical {{
    background: {PAPER_EDGE};
    border-radius: 5px;
    min-height: 24px;
}}

QSlider::groove:horizontal {{
    border: 1px solid {PAPER_EDGE};
    height: 6px;
    background: {PAPER_LIGHT};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}

QProgressBar {{
    border: 1px solid {PAPER_EDGE};
    border-radius: 6px;
    background-color: {PAPER_LIGHT};
    text-align: center;
    color: {INK};
    padding: 1px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 5px;
}}
"""
