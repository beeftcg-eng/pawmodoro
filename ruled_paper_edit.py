"""
ruled_paper_edit.py - A QTextEdit that looks like a page of ruled notebook
paper: faint horizontal lines and a colored left margin rule (both derived
from the current theme palette), scrolling along with the text underneath.
"""
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QTextCharFormat
from PyQt6.QtCore import Qt

import theme

LINE_HEIGHT = 28
MARGIN_X = 44

# Matches the (size, bold) pairs notes_toolbar.py's HEADING_SIZES uses for
# "Heading 1" and "Heading 2" — used to detect "the line just finished was
# a heading" so the next paragraph can drop back to body text.
HEADING_MIN_SIZE = 16


class RuledPaperEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(theme.serif_font(12))
        self.document().setDocumentMargin(MARGIN_X - 4)
        # repaint the ruled lines whenever we scroll, so they track the text
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.refresh_theme()

    def refresh_theme(self):
        """Re-apply colors for the current palette. Call after a theme switch."""
        p = theme.current()
        self._line_color = QColor(p["PAPER_EDGE"])
        self._line_color.setAlpha(150)
        self._margin_color = QColor(p["ACCENT"])
        self._margin_color.setAlpha(170)
        self.setStyleSheet(
            f"QTextEdit {{ background-color: {p['PAPER_LIGHT']}; color: {p['INK']};"
            f" border: 1px solid {p['PAPER_EDGE']}; border-radius: 8px;"
            f" selection-background-color: {p['ACCENT_SOFT']}; }}"
        )
        self.viewport().update()

    def keyPressEvent(self, event):
        # Like Google Docs/Word: pressing Enter at the end of a heading
        # line starts the next paragraph in body text rather than
        # continuing the heading style indefinitely.
        is_return = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        was_heading = is_return and self._current_format_is_heading()
        super().keyPressEvent(event)
        if was_heading:
            body_fmt = QTextCharFormat()
            body_fmt.setFontPointSize(12)
            body_fmt.setFontWeight(QFont.Weight.Normal)
            self.mergeCurrentCharFormat(body_fmt)

    def _current_format_is_heading(self):
        fmt = self.currentCharFormat()
        return fmt.fontWeight() == QFont.Weight.Bold and fmt.fontPointSize() >= HEADING_MIN_SIZE

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.viewport().width()
        h = self.viewport().height()

        line_pen = QPen(self._line_color)
        line_pen.setWidth(1)
        painter.setPen(line_pen)
        offset = self.verticalScrollBar().value() % LINE_HEIGHT
        y = LINE_HEIGHT - offset
        while y < h:
            painter.drawLine(0, y, w, y)
            y += LINE_HEIGHT

        margin_pen = QPen(self._margin_color)
        margin_pen.setWidth(2)
        painter.setPen(margin_pen)
        painter.drawLine(MARGIN_X - 10, 0, MARGIN_X - 10, h)

        painter.end()
        super().paintEvent(event)
