"""Floating status pill. Appearance is a functional placeholder (per DESIGN.md).

Compact and low-opacity. The whole pill fades+slides in when it appears and
fades+slides out when it leaves (done, error timeout, or idle). States:
listening -> smoothed level bars only; processing -> small pulsing dots (no
text); done -> "Done" over a soft dark frosted backdrop; error -> red text.
"""
from __future__ import annotations

import math
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

_BAR_COUNT = 13
_TICK_MS = 33
_APPEAR_S = 0.18
_CLOSE_S = 0.22
_DONE_HOLD_S = 0.9
_ERROR_HOLD_S = 3.0
_SLIDE_PX = 6.0
_PAD = 10


def _ease_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1.0 - (1.0 - p) ** 3


class LevelNormalizer:
    """Auto-gain for the visualizer: scales RMS against a decaying rolling peak.

    Mic gain varies by orders of magnitude between devices (this machine's
    ambient RMS measured ~7e-5); any fixed linear scale leaves the bars flat.
    """

    MIN_PEAK = 0.002  # ~ -54 dB: below this is treated as silence, not signal
    DECAY = 0.985     # per-block peak decay (~1 s half-life at typical block rates)

    def __init__(self):
        self._peak = self.MIN_PEAK

    def level(self, rms_value: float) -> float:
        self._peak = max(self._peak * self.DECAY, self.MIN_PEAK, rms_value)
        return math.sqrt(min(1.0, rms_value / self._peak))


class PillOverlay(QWidget):
    WIDTH, HEIGHT = 72, 26

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self._state = "idle"
        self._message = ""
        self._levels: deque[float] = deque([0.0] * _BAR_COUNT, maxlen=_BAR_COUNT)
        self._shown: list[float] = [0.0] * _BAR_COUNT  # eased display values
        self._normalizer = LevelNormalizer()
        self._t = 0.0          # seconds since the current state began
        self._appear_t = _APPEAR_S  # appear-animation clock (full = appeared)
        self._closing = False
        self._close_t = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(_TICK_MS)
        self._anim.timeout.connect(self._tick)

    # -- slots -----------------------------------------------------------------

    @Slot(str)
    def set_state(self, state: str) -> None:
        if state == "idle":
            if self.isVisible() and not self._closing:
                self._begin_close()
            return
        was_hidden = not self.isVisible() or self._closing
        self._state = state
        self._t = 0.0
        self._closing = False
        self._close_t = 0.0
        if state == "listening":
            self._message = ""
            self._levels.extend([0.0] * _BAR_COUNT)
            self._shown = [0.0] * _BAR_COUNT
            self._normalizer = LevelNormalizer()
        if was_hidden:
            self._appear_t = 0.0
            self.setWindowOpacity(0.0)
            self._reposition()
            self.show()
        if not self._anim.isActive():
            self._anim.start()
        self.update()

    @Slot(float)
    def set_level(self, value: float) -> None:
        # Targets only; the animation tick eases the bars toward them.
        self._levels.append(self._normalizer.level(value))

    @Slot(str)
    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    # -- internals -------------------------------------------------------------

    def _display_text(self) -> str:
        if self._state == "done":
            return "Done"
        if self._state == "error":
            return self._message or "Error"
        return ""  # listening and processing carry no text

    def _reposition(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.WIDTH // 2,
                  geo.bottom() - self.HEIGHT - 24)

    def _begin_close(self) -> None:
        self._closing = True
        self._close_t = 0.0
        if not self._anim.isActive():
            self._anim.start()

    def _finish_close(self) -> None:
        self._anim.stop()
        self.hide()
        self.setWindowOpacity(1.0)
        self._state = "idle"
        self._closing = False

    def _opacity(self) -> float:
        appear = _ease_out(self._appear_t / _APPEAR_S)
        close = _ease_out(self._close_t / _CLOSE_S) if self._closing else 0.0
        return appear * (1.0 - close)

    def _tick(self) -> None:
        dt = _TICK_MS / 1000.0
        self._t += dt
        if self._appear_t < _APPEAR_S:
            self._appear_t = min(_APPEAR_S, self._appear_t + dt)
        # Ease bars toward their targets: snappy attack, soft release.
        for i, target in enumerate(self._levels):
            k = 0.55 if target > self._shown[i] else 0.25
            self._shown[i] += (target - self._shown[i]) * k
        if not self._closing:
            if self._state == "done" and self._t > _DONE_HOLD_S:
                self._begin_close()
            elif self._state == "error" and self._t > _ERROR_HOLD_S:
                self._begin_close()
        if self._closing:
            self._close_t += dt
            if self._close_t >= _CLOSE_S:
                self._finish_close()
                return
        self.setWindowOpacity(self._opacity())
        self.update()

    # -- painting --------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Slide: rise in while appearing, sink out while closing.
        appear = _ease_out(self._appear_t / _APPEAR_S)
        close = _ease_out(self._close_t / _CLOSE_S) if self._closing else 0.0
        painter.translate(0, (1.0 - appear) * _SLIDE_PX + close * _SLIDE_PX)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # Lower fill opacity than before, but a thin edge keeps the pill
        # reading as one solid shape instead of a soft translucent blur.
        painter.setBrush(QColor(6, 6, 8, 80))
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        font = painter.font()
        font.setPointSizeF(8.0)
        painter.setFont(font)

        if self._state == "listening":
            self._paint_bars(painter, rect)
        elif self._state == "processing":
            self._paint_dots(painter, rect)
        elif self._state == "done":
            self._paint_done(painter, rect)
        elif self._state == "error":
            painter.setPen(QColor(255, 99, 99))
            text = painter.fontMetrics().elidedText(
                self._display_text(), Qt.TextElideMode.ElideRight,
                int(rect.width()) - 8)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_bars(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(235, 235, 240, 235))
        pitch = (rect.width() - 2 * _PAD) / _BAR_COUNT
        base_x = rect.left() + _PAD
        mid_y = rect.center().y()
        max_h = rect.height() - 6
        for i, level in enumerate(self._shown):
            h = max(2.0, level * max_h)
            painter.drawRoundedRect(
                QRectF(base_x + i * pitch + (pitch - 2.5) / 2, mid_y - h / 2,
                       2.5, h), 1.25, 1.25)

    def _paint_dots(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        cx, cy = rect.center().x(), rect.center().y()
        spacing, radius = 9.0, 2.6
        phase = self._t * 5.0
        for i in (-1, 0, 1):
            wave = 0.5 + 0.5 * math.sin(phase - i * 0.9)
            painter.setBrush(QColor(235, 235, 240, int(70 + 165 * wave)))
            painter.drawEllipse(QPointF(cx + i * spacing, cy), radius, radius)

    def _paint_done(self, painter: QPainter, rect: QRectF) -> None:
        progress = min(1.0, self._t / 0.2)  # quick fade-in of the badge + word
        # Soft, blurry-looking dark backdrop: stacked translucent rounded rects
        # of decreasing size fake a blurred panel behind the word.
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(5):
            inset = i * 0.9
            painter.setBrush(QColor(0, 0, 0, int(28 * progress)))
            r = rect.adjusted(inset, inset, -inset, -inset)
            painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        # Soft green glow of the word (offset copies) + crisp text on top.
        green = QColor(150, 235, 170)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.setPen(QColor(green.red(), green.green(), green.blue(),
                                  int(45 * progress)))
            painter.drawText(rect.translated(dx, dy),
                             Qt.AlignmentFlag.AlignCenter, "Done")
        painter.setPen(QColor(green.red(), green.green(), green.blue(),
                              int(255 * progress)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Done")
