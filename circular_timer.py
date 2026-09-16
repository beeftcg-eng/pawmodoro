"""
circular_timer.py - A ring-shaped progress display for the pomodoro timer.
Draws a circular track plus a clockwise progress arc, with the countdown
and phase name centered inside it, replacing the old plain "label over
big digits" stack with something that shows progress at a glance.
"""
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtGui import QPainter, QPen, QColor, QFont
from PyQt6.QtCore import Qt, QRectF

import theme

FULL_TURN = 360 * 16  # QPainter angles are in 1/16th of a degree


class CircularTimer(QWidget):
    """A self-contained progress ring. Caller drives it entirely through
    set_progress()/set_colors(); it holds no timer or pomodoro state of
    its own, so the same widget works for the full tab and the compact
    desktop-widget view alike."""

    def __init__(self, parent=None, min_side=150):
        super().__init__(parent)
        p = theme.current()
        self._fraction = 0.0  # 0..1, how much of the current phase has elapsed
        self._time_text = "00:00"
        self._phase_text = "Work session"
        self._ring_color = QColor(p["ACCENT"])
        self._track_color = QColor(p["PAPER_EDGE"])
        self._ink_color = QColor(p["INK"])
        self._ink_soft_color = QColor(p["INK_SOFT"])
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(min_side, min_side)

    def set_progress(self, fraction, time_text, phase_text):
        self._fraction = max(0.0, min(1.0, fraction))
        self._time_text = time_text
        self._phase_text = phase_text
        self.update()

    def set_colors(self, ring_color, track_color, ink_color, ink_soft_color):
        self._ring_color = QColor(ring_color)
        self._track_color = QColor(track_color)
        self._ink_color = QColor(ink_color)
        self._ink_soft_color = QColor(ink_soft_color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height())
        if side <= 0:
            return
        pen_width = max(6, int(side * 0.055))
        margin = pen_width / 2 + max(4, int(side * 0.03))
        rect = QRectF(
            (self.width() - side) / 2 + margin,
            (self.height() - side) / 2 + margin,
            side - 2 * margin,
            side - 2 * margin,
        )

        track_pen = QPen(self._track_color)
        track_pen.setWidthF(pen_width)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, FULL_TURN)

        if self._fraction > 0:
            arc_pen = QPen(self._ring_color)
            arc_pen.setWidthF(pen_width)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            # start at 12 o'clock, sweep clockwise as the phase progresses
            painter.drawArc(rect, 90 * 16, int(-self._fraction * FULL_TURN))

        painter.setPen(self._ink_color)
        time_font = QFont()
        time_font.setFamilies(theme.font_family_stack())
        time_font.setBold(True)
        time_font.setPointSizeF(max(16, side * 0.155))
        painter.setFont(time_font)
        time_rect = QRectF(rect.x(), rect.center().y() - side * 0.15, rect.width(), side * 0.2)
        painter.drawText(time_rect, Qt.AlignmentFlag.AlignCenter, self._time_text)

        painter.setPen(self._ink_soft_color)
        phase_font = QFont()
        phase_font.setFamilies(theme.font_family_stack())
        phase_font.setPointSizeF(max(9, side * 0.05))
        painter.setFont(phase_font)
        phase_rect = QRectF(rect.x(), rect.center().y() + side * 0.04, rect.width(), side * 0.12)
        painter.drawText(phase_rect, Qt.AlignmentFlag.AlignCenter, self._phase_text)
