"""Floating status pill. Appearance is a functional placeholder (per DESIGN.md).

States: listening -> bars only (no text); processing -> shimmering text;
done -> bars fade out under a frosted "Done" badge, then the pill fades away;
error -> red message, auto-hides.
"""
from __future__ import annotations

import math
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

_BAR_COUNT = 24
_TICK_MS = 33
_DONE_CROSSFADE_S = 0.35
_DONE_HOLD_S = 0.6
_DONE_FADE_S = 0.4


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
    WIDTH, HEIGHT = 160, 30

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
        self._normalizer = LevelNormalizer()
        self._t = 0.0  # seconds since the current state began
        self._anim = QTimer(self)
        self._anim.setInterval(_TICK_MS)
        self._anim.timeout.connect(self._tick)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._dismiss)

    # -- slots -----------------------------------------------------------------

    @Slot(str)
    def set_state(self, state: str) -> None:
        self._state = state
        self._t = 0.0
        self._hide_timer.stop()
        self._anim.stop()
        self.setWindowOpacity(1.0)
        if state == "idle":
            self.hide()
            return
        if state == "listening":
            self._message = ""
            self._levels.extend([0.0] * _BAR_COUNT)
            self._normalizer = LevelNormalizer()
        elif state in ("processing", "done"):
            self._anim.start()
        elif state == "error":
            self._hide_timer.start(3000)
        self._reposition()
        self.show()
        self.update()

    @Slot(float)
    def set_level(self, value: float) -> None:
        self._levels.append(self._normalizer.level(value))
        if self._state == "listening":
            self.update()

    @Slot(str)
    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    # -- internals ---------------------------------------------------------------

    def _display_text(self) -> str:
        if self._state == "processing":
            return "Transcribing"
        if self._state == "done":
            return "Done"
        if self._state == "error":
            return self._message or "Error"
        return ""

    def _reposition(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.WIDTH // 2,
                  geo.bottom() - self.HEIGHT - 24)

    def _dismiss(self) -> None:
        self._anim.stop()
        self.hide()
        self.setWindowOpacity(1.0)
        self._state = "idle"

    def _tick(self) -> None:
        self._t += _TICK_MS / 1000.0
        if self._state == "done":
            fade_start = _DONE_CROSSFADE_S + _DONE_HOLD_S
            if self._t > fade_start:
                fade = (self._t - fade_start) / _DONE_FADE_S
                if fade >= 1.0:
                    self._dismiss()
                    return
                self.setWindowOpacity(1.0 - fade)
        self.update()

    # -- painting ----------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 24, 150))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        font = painter.font()
        font.setPointSizeF(8.5)
        painter.setFont(font)

        if self._state == "listening":
            self._paint_bars(painter, rect, 255)
        elif self._state == "processing":
            self._paint_shimmer_text(painter, rect)
        elif self._state == "done":
            progress = min(1.0, self._t / _DONE_CROSSFADE_S)
            self._paint_bars(painter, rect, int(255 * (1.0 - progress)))
            if progress > 0.0:
                self._paint_done(painter, rect, progress)
        elif self._state == "error":
            painter.setPen(QColor(255, 99, 99))
            text = painter.fontMetrics().elidedText(
                self._display_text(), Qt.TextElideMode.ElideRight,
                int(rect.width()) - 24)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_bars(self, painter: QPainter, rect: QRectF, alpha: int) -> None:
        if alpha <= 0:
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(235, 235, 240, alpha))
        pitch = (rect.width() - 24) / _BAR_COUNT
        base_x = rect.left() + 12
        mid_y = rect.center().y()
        max_h = rect.height() - 10
        for i, level in enumerate(self._levels):
            h = max(2.0, level * max_h)
            painter.drawRoundedRect(
                QRectF(base_x + i * pitch + (pitch - 3) / 2, mid_y - h / 2, 3, h),
                1.5, 1.5)

    def _paint_shimmer_text(self, painter: QPainter, rect: QRectF) -> None:
        text = self._display_text()
        metrics = painter.fontMetrics()
        x = rect.center().x() - metrics.horizontalAdvance(text) / 2
        baseline = rect.center().y() + metrics.capHeight() / 2
        phase = self._t * 6.0
        for i, char in enumerate(text):
            wave = 0.5 + 0.5 * math.sin(phase - i * 0.55)
            painter.setPen(QColor(235, 235, 240, int(90 + 165 * wave)))
            painter.drawText(QPointF(x, baseline), char)
            x += metrics.horizontalAdvance(char)

    def _paint_done(self, painter: QPainter, rect: QRectF,
                    progress: float) -> None:
        # frosted badge behind the word, covering the fading bars
        overlay = rect.adjusted(rect.width() * 0.28, 4,
                                -rect.width() * 0.28, -4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(240, 240, 245, int(70 * progress)))
        painter.drawRoundedRect(overlay, overlay.height() / 2,
                                overlay.height() / 2)
        painter.setPen(QColor(140, 230, 160, int(255 * progress)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Done")
