"""
teleprompter_window.py - The prompter: a black window (full screen on the
chosen monitor by default) that scrolls the script past a reading line at
the set words-per-minute pace (teleprompter.scroll_speed).

Keys: Space / click start and pause (a 3-2-1 countdown first, from the
top), Up/Down speed, +/- text size, Left/Right a line back/forward,
PageUp/PageDown half a screen, Home back to the top, M mirror, F / F11 /
double-click full screen, H hide the help, Esc close. The mouse wheel
scrolls by hand. Speed, size and mirror are saved as you change them.

Mirror flips everything left-right for a beam-splitter (glass) prompter,
where you read the screen's reflection.
"""
import math

from PyQt6.QtCore import Qt, QTimer, QElapsedTimer, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPalette, QTextBlockFormat, QTextCursor, QTextDocument,
    QAbstractTextDocumentLayout,
)
from PyQt6.QtWidgets import QWidget

import teleprompter

BACKGROUND = QColor("#000000")
TEXT = "#ffffff"
CUE = "#f5c542"
HUD = QColor(255, 255, 255, 120)
READING_LINE = 0.33  # the reading line, as a fraction of the height from the top
FRAME_MS = 16
LINE_SPACING = 115  # percent


class TeleprompterWindow(QWidget):
    closed = pyqtSignal()

    def __init__(self, storage, text, title="", parent=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"Teleprompter — {title}" if title else "Teleprompter")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, BACKGROUND)
        self.setPalette(palette)
        self.setMinimumSize(320, 240)

        self.storage = storage
        self.settings = storage.get_teleprompter_settings()
        self.text = text
        self.words = teleprompter.word_count(text)
        self.offset = 0.0  # pixels of text scrolled past the reading line
        self.running = False
        self.countdown_left = None  # seconds, while counting down
        self.elapsed = 0.0  # seconds spent scrolling
        self.show_help = True

        self.doc = QTextDocument(self)
        self.doc.setDocumentMargin(0)
        self._build_document()

        self.frame_timer = QTimer(self)
        self.frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.frame_timer.timeout.connect(self._on_frame)
        self.clock = QElapsedTimer()

    # ---------- Opening ----------
    def open_on(self, screen_name, fallback_screen=None):
        """Shows the window on the monitor called `screen_name` (else `fallback_screen`)."""
        screens = QGuiApplication.screens()
        screen = next((s for s in screens if s.name() == screen_name), None) or fallback_screen \
            or QGuiApplication.primaryScreen()
        if screen is not None:
            self.setScreen(screen)
            area = screen.availableGeometry()
            width, height = min(1100, int(area.width() * 0.75)), min(700, int(area.height() * 0.75))
            self.setGeometry(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2,
                             width, height)
        if self.settings["fullscreen"]:
            self.showFullScreen()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    # ---------- Layout ----------
    def _build_document(self):
        font = QFont(self.font())
        font.setPixelSize(self.settings["font_size"])
        font.setWeight(QFont.Weight.DemiBold)
        self.doc.setDefaultFont(font)
        self.doc.setDefaultStyleSheet(f"body, p {{ color: {TEXT}; }}")
        self.doc.setHtml(teleprompter.script_html(self.text, CUE))
        cursor = QTextCursor(self.doc)
        cursor.select(QTextCursor.SelectionType.Document)
        block = QTextBlockFormat()
        block.setLineHeight(LINE_SPACING, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        block.setBottomMargin(self.settings["font_size"] * 0.25)
        cursor.mergeBlockFormat(block)
        self._layout_document()

    def _layout_document(self):
        self.doc.setTextWidth(max(100, self.width() - 2 * self._margin()))
        self.offset = min(self.offset, self._max_offset())

    def _margin(self):
        return self.width() * self.settings["margin"] / 100

    def _line_height(self):
        return QFontMetricsF(self.doc.defaultFont()).height() * LINE_SPACING / 100

    def _max_offset(self):
        return max(0.0, self.doc.size().height() - self._line_height())

    def speed(self):
        """Scroll speed in pixels per second."""
        return teleprompter.scroll_speed(self.settings["wpm"], self.doc.size().height(), self.words)

    def resizeEvent(self, event):
        # Keep the line being read on the reading line when the width (and so the wrapping) changes.
        old_height = self.doc.size().height()
        fraction = self.offset / old_height if old_height else 0
        self._layout_document()
        self.offset = min(fraction * self.doc.size().height(), self._max_offset())
        super().resizeEvent(event)

    # ---------- Playing ----------
    def toggle(self):
        if self.running or self.countdown_left is not None:
            self.pause()
        else:
            self.play()

    def play(self):
        if self.offset >= self._max_offset() and self._max_offset() > 0:
            self.offset, self.elapsed = 0.0, 0.0  # finished: start again from the top
        if self.offset == 0 and self.settings["countdown"]:
            self.countdown_left = float(self.settings["countdown"])
        self.running = True
        self.clock.start()
        self.frame_timer.start(FRAME_MS)
        if self.isFullScreen():
            self.setCursor(Qt.CursorShape.BlankCursor)
        self.update()

    def pause(self):
        self.running = False
        self.countdown_left = None
        self.frame_timer.stop()
        self.unsetCursor()
        self.update()

    def _on_frame(self):
        self.advance(self.clock.restart() / 1000.0)

    def advance(self, seconds):
        """Moves the prompter on by `seconds` of time."""
        if not self.running:
            return
        if self.countdown_left is not None:
            self.countdown_left -= seconds
            if self.countdown_left > 0:
                self.update()
                return
            seconds, self.countdown_left = -self.countdown_left, None
        self.offset += self.speed() * seconds
        self.elapsed += seconds
        if self.offset >= self._max_offset():
            self.offset = self._max_offset()
            self.pause()
        self.update()

    def scroll_by(self, pixels):
        self.offset = max(0.0, min(self._max_offset(), self.offset + pixels))
        self.update()

    def restart(self):
        self.pause()
        self.offset, self.elapsed = 0.0, 0.0
        self.update()

    # ---------- Settings ----------
    def _save(self, **changes):
        self.storage.set_teleprompter_settings(**changes)
        self.settings = self.storage.get_teleprompter_settings()

    def change_speed(self, steps):
        self._save(wpm=self.settings["wpm"] + steps * teleprompter.WPM_STEP)
        self.update()

    def change_font(self, steps):
        fraction = self.offset / self.doc.size().height() if self.doc.size().height() else 0
        self._save(font_size=self.settings["font_size"] + steps * teleprompter.FONT_STEP)
        self._build_document()
        self.offset = min(fraction * self.doc.size().height(), self._max_offset())
        self.update()

    def toggle_mirror(self):
        self._save(mirror=not self.settings["mirror"])
        self.update()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.unsetCursor()
        else:
            self.showFullScreen()
        self._save(fullscreen=self.isFullScreen())

    # ---------- Input ----------
    def keyPressEvent(self, event):
        key = event.key()
        line = self._line_height()
        actions = {
            Qt.Key.Key_Space: self.toggle,
            Qt.Key.Key_Up: lambda: self.change_speed(1),
            Qt.Key.Key_Down: lambda: self.change_speed(-1),
            Qt.Key.Key_Plus: lambda: self.change_font(1),
            Qt.Key.Key_Equal: lambda: self.change_font(1),
            Qt.Key.Key_Minus: lambda: self.change_font(-1),
            Qt.Key.Key_Left: lambda: self.scroll_by(-line),
            Qt.Key.Key_Right: lambda: self.scroll_by(line),
            Qt.Key.Key_PageUp: lambda: self.scroll_by(-self.height() / 2),
            Qt.Key.Key_PageDown: lambda: self.scroll_by(self.height() / 2),
            Qt.Key.Key_Home: self.restart,
            Qt.Key.Key_M: self.toggle_mirror,
            Qt.Key.Key_F: self.toggle_fullscreen,
            Qt.Key.Key_F11: self.toggle_fullscreen,
            Qt.Key.Key_H: self._toggle_help,
            Qt.Key.Key_Escape: self.close,
        }
        action = actions.get(key)
        if action is None:
            super().keyPressEvent(event)
        else:
            action()

    def _toggle_help(self):
        self.show_help = not self.show_help
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()

    def mouseDoubleClickEvent(self, event):
        self.toggle()  # undo the first click's toggle
        self.toggle_fullscreen()

    def wheelEvent(self, event):
        self.scroll_by(-event.angleDelta().y() / 120 * self._line_height())

    def closeEvent(self, event):
        self.frame_timer.stop()
        self.closed.emit()
        super().closeEvent(event)

    # ---------- Painting ----------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        width, height = self.width(), self.height()
        if self.settings["mirror"]:
            painter.translate(width, 0)
            painter.scale(-1, 1)
        reading_y = height * READING_LINE
        line = self._line_height()

        # The band the line being read sits in, with a marker at each edge.
        painter.fillRect(QRectF(0, reading_y - line * 0.1, width, line * 1.2), QColor(255, 255, 255, 18))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CUE))
        mid = reading_y + line * 0.5
        size = min(line * 0.3, 18)
        for x, direction in ((4, 1), (width - 4, -1)):
            painter.drawPolygon(QPointF(x, mid - size), QPointF(x + direction * size * 1.4, mid),
                                QPointF(x, mid + size))

        painter.save()
        painter.translate(self._margin(), reading_y - self.offset)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
        context.clip = QRectF(0, self.offset - reading_y, self.doc.textWidth(), height)
        self.doc.documentLayout().draw(painter, context)
        painter.restore()

        if self.countdown_left is not None:
            painter.setPen(QColor(CUE))
            font = QFont(self.font())
            font.setPixelSize(int(min(width, height) * 0.35))
            font.setBold(True)
            painter.setFont(font)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 170))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(math.ceil(self.countdown_left)))

        self._paint_hud(painter, width, height)
        painter.end()

    def _paint_hud(self, painter, width, height):
        # A thin progress bar along the bottom, and the speed / time / keys.
        maximum = self._max_offset()
        progress = self.offset / maximum if maximum else 0
        painter.fillRect(QRectF(0, height - 4, width * progress, 4), QColor(CUE))
        if not self.show_help and self.running:
            return
        painter.fillRect(QRectF(0, height - 40, width, 36), QColor(0, 0, 0, 220))
        total = teleprompter.read_seconds(self.text, self.settings["wpm"])
        status = "▶" if self.running else ("✓ the end" if maximum and self.offset >= maximum else "⏸")
        parts = [status, f"{self.settings['wpm']} wpm",
                 f"{teleprompter.format_duration(self.elapsed)} / {teleprompter.format_duration(total)}"]
        if self.settings["mirror"]:
            parts.append("mirrored")
        if self.show_help and not self.running:
            parts.append("Space start/pause · ↑↓ speed · +/− size · ←→ nudge · Home top · M mirror · "
                         "F full screen · H hide this · Esc close")
        font = QFont(self.font())
        font.setPixelSize(15)
        painter.setFont(font)
        painter.setPen(HUD)
        painter.drawText(QRectF(14, height - 34, width - 28, 24),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "   ".join(parts))
