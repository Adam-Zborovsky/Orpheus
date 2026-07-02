"""Floating status pill. Appearance is a functional placeholder (per DESIGN.md)."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QRectF, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

STATE_LABELS = {
    "listening": "Listening",
    "processing": "Transcribing…",
    "done": "Done",
    "error": "Error",
}

_BAR_COUNT = 24


class PillOverlay(QWidget):
    WIDTH, HEIGHT = 280, 48

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
        self._fade = QTimer(self)
        self._fade.setSingleShot(True)
        self._fade.timeout.connect(self.hide)

    def _reposition(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(geo.center().x() - self.WIDTH // 2,
                  geo.bottom() - self.HEIGHT - 24)

    @Slot(str)
    def set_state(self, state: str) -> None:
        self._state = state
        self._fade.stop()
        if state == "idle":
            self.hide()
            return
        if state == "listening":
            self._message = ""
            self._levels.extend([0.0] * _BAR_COUNT)
        self._reposition()
        self.show()
        if state == "done":
            self._fade.start(1600)
        elif state == "error":
            self._fade.start(3000)
        self.update()

    @Slot(float)
    def set_level(self, value: float) -> None:
        self._levels.append(min(1.0, value * 8.0))  # scale RMS for visibility
        if self._state == "listening":
            self.update()

    @Slot(str)
    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 24, 230))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        text_color = {
            "error": QColor(255, 99, 99),
            "done": QColor(120, 220, 140),
        }.get(self._state, QColor(235, 235, 240))
        painter.setPen(text_color)
        label = self._message or STATE_LABELS.get(self._state, "")
        bars_width = _BAR_COUNT * 4 if self._state == "listening" else 0
        text_rect = rect.adjusted(20, 0, -(24 + bars_width), 0)
        label = painter.fontMetrics().elidedText(
            label, Qt.TextElideMode.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter
                         | Qt.AlignmentFlag.AlignLeft, label)

        if self._state == "listening":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(235, 235, 240))
            base_x = rect.right() - 16 - _BAR_COUNT * 4
            mid_y = rect.center().y()
            for i, level in enumerate(self._levels):
                height = max(2.0, level * (rect.height() - 16))
                painter.drawRoundedRect(
                    QRectF(base_x + i * 4, mid_y - height / 2, 3, height),
                    1.5, 1.5)
