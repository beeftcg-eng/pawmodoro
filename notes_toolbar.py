"""
notes_toolbar.py - A small rich-text formatting toolbar wired directly to a
RuledPaperEdit instance: bold, underline, text color and highlight color
(themed swatches plus custom pickers), heading levels, and basic
bullet/numbered lists.
"""
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QToolButton, QComboBox, QMenu,
    QColorDialog
)
from PyQt6.QtGui import QFont, QTextCharFormat, QTextListFormat, QTextCursor, QColor, QBrush, QIcon, QPixmap
from PyQt6.QtCore import Qt

# A handful of "ink" colors that fit the pen-and-paper theme
INK_SWATCHES = [
    ("Black ink", "#3a2f22"),
    ("Red ink", "#96433a"),
    ("Navy ink", "#2c4a6e"),
    ("Forest ink", "#3f6b3f"),
    ("Gold ink", "#a67c27"),
]

# Highlighter-style background colors (soft/pastel, like a real highlighter
# pen on paper rather than a saturated digital color)
HIGHLIGHT_SWATCHES = [
    ("Yellow", "#fff59d"),
    ("Green", "#c8e6c9"),
    ("Pink", "#f8bbd0"),
    ("Blue", "#bbdefb"),
    ("Orange", "#ffe0b2"),
]

HEADING_SIZES = {
    "Body text": (12, False),
    "Heading 1": (20, True),
    "Heading 2": (16, True),
}


def _swatch_icon(hex_color):
    pix = QPixmap(14, 14)
    pix.fill(QColor(hex_color))
    return QIcon(pix)


class NotesToolbar(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Heading level
        self.heading_combo = QComboBox()
        self.heading_combo.addItems(list(HEADING_SIZES.keys()))
        self.heading_combo.setFixedWidth(110)
        self.heading_combo.activated.connect(self._apply_heading)
        layout.addWidget(self.heading_combo)

        # Bold
        self.bold_btn = QToolButton()
        self.bold_btn.setText("B")
        bold_font = QFont()
        bold_font.setBold(True)
        self.bold_btn.setFont(bold_font)
        self.bold_btn.setCheckable(True)
        self.bold_btn.clicked.connect(self._toggle_bold)
        layout.addWidget(self.bold_btn)

        # Underline
        self.underline_btn = QToolButton()
        self.underline_btn.setText("U")
        underline_font = QFont()
        underline_font.setUnderline(True)
        self.underline_btn.setFont(underline_font)
        self.underline_btn.setCheckable(True)
        self.underline_btn.clicked.connect(self._toggle_underline)
        layout.addWidget(self.underline_btn)

        # Color picker (button with dropdown menu of ink swatches + custom)
        self.color_btn = QToolButton()
        self.color_btn.setText("A")
        self.color_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        color_menu = QMenu(self.color_btn)
        for name, hex_color in INK_SWATCHES:
            action = color_menu.addAction(_swatch_icon(hex_color), name)
            action.triggered.connect(lambda checked, c=hex_color: self._apply_color(c))
        color_menu.addSeparator()
        custom_action = color_menu.addAction("Custom color\u2026")
        custom_action.triggered.connect(self._pick_custom_color)
        self.color_btn.setMenu(color_menu)
        layout.addWidget(self.color_btn)

        # Highlight (background color behind text, like a highlighter pen)
        self.highlight_btn = QToolButton()
        self.highlight_btn.setText("\U0001F58D")
        self.highlight_btn.setToolTip("Highlight")
        self.highlight_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        highlight_menu = QMenu(self.highlight_btn)
        for name, hex_color in HIGHLIGHT_SWATCHES:
            action = highlight_menu.addAction(_swatch_icon(hex_color), name)
            action.triggered.connect(lambda checked, c=hex_color: self._apply_highlight(c))
        highlight_menu.addSeparator()
        custom_highlight_action = highlight_menu.addAction("Custom color\u2026")
        custom_highlight_action.triggered.connect(self._pick_custom_highlight)
        no_highlight_action = highlight_menu.addAction("No highlight")
        no_highlight_action.triggered.connect(lambda: self._apply_highlight(None))
        self.highlight_btn.setMenu(highlight_menu)
        layout.addWidget(self.highlight_btn)

        # Lists
        bullet_btn = QToolButton()
        bullet_btn.setText("\u2022 List")
        bullet_btn.clicked.connect(self._bullet_list)
        layout.addWidget(bullet_btn)

        numbered_btn = QToolButton()
        numbered_btn.setText("1. List")
        numbered_btn.clicked.connect(self._numbered_list)
        layout.addWidget(numbered_btn)

        # Clear formatting
        clear_btn = QToolButton()
        clear_btn.setText("Clear")
        clear_btn.setToolTip("Clear formatting on the current selection")
        clear_btn.clicked.connect(self._clear_formatting)
        layout.addWidget(clear_btn)

        layout.addStretch()

        # keep Bold/Underline buttons in sync with wherever the cursor is
        self.editor.currentCharFormatChanged.connect(self._sync_buttons)

    # ---------- helpers ----------
    def _merge_format(self, fmt):
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)

    def _sync_buttons(self, fmt):
        self.bold_btn.setChecked(fmt.fontWeight() == QFont.Weight.Bold)
        self.underline_btn.setChecked(fmt.fontUnderline())
        self._sync_heading_combo(fmt)

    def _sync_heading_combo(self, fmt):
        """Keeps the heading dropdown showing whatever style the cursor is
        actually sitting in (including right after auto-reverting to body
        text at the start of a new paragraph), instead of only updating
        when the user picks from the dropdown themselves."""
        size = int(fmt.fontPointSize()) if fmt.fontPointSize() > 0 else 12
        bold = fmt.fontWeight() == QFont.Weight.Bold
        for name, (want_size, want_bold) in HEADING_SIZES.items():
            if size == want_size and bold == want_bold:
                self.heading_combo.blockSignals(True)
                self.heading_combo.setCurrentText(name)
                self.heading_combo.blockSignals(False)
                return

    # ---------- actions ----------
    def _toggle_bold(self):
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if self.bold_btn.isChecked() else QFont.Weight.Normal)
        self._merge_format(fmt)

    def _toggle_underline(self):
        fmt = QTextCharFormat()
        fmt.setFontUnderline(self.underline_btn.isChecked())
        self._merge_format(fmt)

    def _apply_color(self, hex_color):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(hex_color))
        self._merge_format(fmt)

    def _pick_custom_color(self):
        color = QColorDialog.getColor(self.editor.textColor(), self, "Pick text color")
        if color.isValid():
            self._apply_color(color.name())

    def _apply_highlight(self, hex_color):
        fmt = QTextCharFormat()
        if hex_color is None:
            # explicitly set to "no brush" rather than leaving the property
            # unset, so merging this actually clears an existing highlight
            # instead of just failing to add a new one
            fmt.setBackground(QBrush(Qt.BrushStyle.NoBrush))
        else:
            fmt.setBackground(QColor(hex_color))
        self._merge_format(fmt)

    def _pick_custom_highlight(self):
        color = QColorDialog.getColor(QColor("#fff59d"), self, "Pick highlight color")
        if color.isValid():
            self._apply_highlight(color.name())

    def _apply_heading(self, index):
        label = self.heading_combo.itemText(index)
        size, bold = HEADING_SIZES[label]
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        fmt.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        cursor.mergeCharFormat(fmt)
        self.editor.setTextCursor(cursor)
        self.editor.mergeCurrentCharFormat(fmt)

    def _bullet_list(self):
        fmt = QTextListFormat()
        fmt.setStyle(QTextListFormat.Style.ListDisc)
        self.editor.textCursor().createList(fmt)

    def _numbered_list(self):
        fmt = QTextListFormat()
        fmt.setStyle(QTextListFormat.Style.ListDecimal)
        self.editor.textCursor().createList(fmt)

    def _clear_formatting(self):
        cursor = self.editor.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Normal)
        fmt.setFontUnderline(False)
        fmt.setFontPointSize(12)
        fmt.setForeground(QColor("#3a2f22"))
        fmt.setBackground(QBrush(Qt.BrushStyle.NoBrush))
        if cursor.hasSelection():
            cursor.setCharFormat(fmt)
        self.editor.setCurrentCharFormat(fmt)
