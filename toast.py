"""
toast.py - A small, non-blocking celebration banner that fades in for a
couple of seconds when something worth celebrating happens (a pomodoro
finishing, a task getting checked off, leveling up, a daily quest wrapping
up). Multiple celebrations queue up rather than stepping on each other, so
e.g. a task completion followed by a quest completion both get shown.
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGraphicsOpacityEffect
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve

import theme

SHOW_MS = 2600
FADE_MS = 220


class CelebrationToast(QWidget):
    """Overlays its parent, floating at the top-center. Call show_toast()
    from anywhere; it queues automatically and repositions itself as the
    parent resizes."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._queue = []
        self._visible = False
        self._fade_anim = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(2)

        self.title_label = QLabel("")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.detail_label = QLabel("")
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.detail_label)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fade_out)

        self.refresh_theme()
        self.hide()

    def refresh_theme(self):
        p = theme.current()
        self.title_label.setFont(theme.serif_font(13, bold=True))
        self.detail_label.setFont(theme.serif_font(10))
        self.title_label.setStyleSheet(f"color: {p['PAPER_LIGHT']}; background: transparent;")
        self.detail_label.setStyleSheet(f"color: {p['PAPER_LIGHT']}; background: transparent;")
        self.setStyleSheet(
            f"CelebrationToast {{ background-color: {p['ACCENT']}; border-radius: 12px; }}"
        )

    def show_toast(self, title, detail=""):
        self._queue.append((title, detail))
        if not self._visible:
            self._advance_queue()

    def _advance_queue(self):
        if not self._queue:
            self._visible = False
            return
        self._visible = True
        title, detail = self._queue.pop(0)
        self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()

        self._opacity_effect.setOpacity(0.0)
        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_anim.setDuration(FADE_MS)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.start()

        self._hide_timer.start(SHOW_MS)

    def _start_fade_out(self):
        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_anim.setDuration(FADE_MS)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_anim.finished.connect(self._on_faded_out)
        self._fade_anim.start()

    def _on_faded_out(self):
        self.hide()
        self._advance_queue()

    def _reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        x = (parent.width() - self.width()) // 2
        self.move(max(0, x), 16)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()
