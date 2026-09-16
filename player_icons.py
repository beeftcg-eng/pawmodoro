"""
player_icons.py - Draws the play/pause/prev/next transport icons directly
with QPainter instead of relying on Unicode media-control glyphs (the
U+23E9-U+23EF block), which have notoriously poor font coverage and were
rendering as blank lines/boxes on some systems. This way it's guaranteed
to look the same everywhere, and matches the app's ink color.
"""
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QPolygonF
from PyQt6.QtCore import Qt, QPointF


def _make_icon(draw_fn, size, color):
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(Qt.PenStyle.NoPen)
    draw_fn(painter, size)
    painter.end()
    return QIcon(pix)


def play_icon(color="#3a2f22", size=20):
    def draw(p, s):
        m = s * 0.24
        tri = QPolygonF([QPointF(m, m * 0.5), QPointF(m, s - m * 0.5), QPointF(s - m * 0.6, s / 2)])
        p.drawPolygon(tri)
    return _make_icon(draw, size, color)


def pause_icon(color="#3a2f22", size=20):
    def draw(p, s):
        bar_w = s * 0.18
        gap = s * 0.14
        top = s * 0.22
        bottom = s * 0.78
        left_x = s / 2 - gap / 2 - bar_w
        right_x = s / 2 + gap / 2
        p.drawRect(int(left_x), int(top), int(bar_w), int(bottom - top))
        p.drawRect(int(right_x), int(top), int(bar_w), int(bottom - top))
    return _make_icon(draw, size, color)


def next_icon(color="#3a2f22", size=20):
    def draw(p, s):
        m = s * 0.22
        tri = QPolygonF([QPointF(m, m * 0.5), QPointF(m, s - m * 0.5), QPointF(s / 2, s / 2)])
        p.drawPolygon(tri)
        bar_x = s / 2
        bar_w = s * 0.14
        p.drawRect(int(bar_x), int(m * 0.5), int(bar_w), int(s - m))
    return _make_icon(draw, size, color)


def prev_icon(color="#3a2f22", size=20):
    def draw(p, s):
        m = s * 0.22
        tri = QPolygonF([QPointF(s - m, m * 0.5), QPointF(s - m, s - m * 0.5), QPointF(s / 2, s / 2)])
        p.drawPolygon(tri)
        bar_w = s * 0.14
        bar_x = s / 2 - bar_w
        p.drawRect(int(bar_x), int(m * 0.5), int(bar_w), int(s - m))
    return _make_icon(draw, size, color)
