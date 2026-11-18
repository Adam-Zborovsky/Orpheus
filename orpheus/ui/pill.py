"""Compact, accessible status pill for Orpheus dictation state."""
from __future__ import annotations

import ctypes
import math
import sys
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Slot
from PySide6.QtGui import (
    QAccessible, QAccessibleEvent, QBrush, QColor, QFontMetricsF,
    QGuiApplication, QLinearGradient, QPainter, QPen, QScreen,
)
from PySide6.QtWidgets import QWidget

try:
    from PySide6.QtGui import QAccessibleAnnouncementEvent
except ImportError:  # Qt 6.7 still exposes the name/description change events.
    QAccessibleAnnouncementEvent = None

_BAR_COUNT = 11
_TICK_MS = 33
_APPEAR_S = 0.16
_CLOSE_S = 0.18
_DONE_HOLD_MS = 1_200
_CANCELLED_HOLD_MS = 1_100
_SLIDE_PX = 4.0

# Opaque surface makes the small status label independent of the wallpaper or
# application behind it. Text/background contrast is over 15:1.
_SURFACE = QColor(20, 22, 26)
_EDGE = QColor(126, 132, 142, 65)
_TEXT = QColor(248, 250, 252)
_ACCENT = QColor(137, 231, 160)
_ERROR_ACCENT = QColor(255, 138, 128)

_LABELS = {
    "listening": "Recording",
    "processing": "Transcribing",
    "transcribing": "Transcribing",
    "cleaning": "Cleaning up",
    "delivering": "Inserting text",
    "done": "Done",
    "error": "Needs attention",
    "cancelled": "Cancelled",
    "shutting_down": "Shutting down",
}


def _ease_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1.0 - (1.0 - p) ** 3


def _client_area_animations_enabled() -> bool:
    """Read Windows' client-area animation preference without changing it."""
    if sys.platform != "win32":
        return True
    try:
        from ctypes import wintypes

        enabled = wintypes.BOOL()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x1042, 0, ctypes.byref(enabled), 0  # SPI_GETCLIENTAREAANIMATION
        )
        return bool(enabled.value) if ok else False
    except (AttributeError, OSError):
        return False


class LevelNormalizer:
    """Map RMS logarithmically from quiet room noise to loud speech.

    A fixed -72 to -24 dBFS range preserves dynamics: sustained speech
    cannot redefine itself as full scale, unlike rolling peak auto-gain.
    """

    def level(self, rms_value: float) -> float:
        if not math.isfinite(rms_value) or rms_value <= 0:
            return 0.0
        return min(1.0, max(0.0, (20 * math.log10(rms_value) + 72) / 48))


class PillOverlay(QWidget):
    WIDTH, HEIGHT = 176, 36

    def __init__(self, reduced_motion: bool | None = None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("Orpheus status")
        self._motion_override = reduced_motion
        self._reduced_motion = (not _client_area_animations_enabled()
                                if reduced_motion is None else bool(reduced_motion))
        self._state = "idle"
        self._message = ""
        self._target_screen: QScreen | None = None
        self._screen_geometry_slot = None
        self._levels: deque[float] = deque([0.0] * _BAR_COUNT, maxlen=_BAR_COUNT)
        self._shown: list[float] = [0.0] * _BAR_COUNT
        self._normalizer = LevelNormalizer()
        self._t = 0.0
        self._appear_t = _APPEAR_S
        self._closing = False
        self._close_t = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(_TICK_MS)
        self._anim.timeout.connect(self._tick)
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(lambda: self.set_state("idle"))

        app = QGuiApplication.instance()
        if app is not None:
            app.screenRemoved.connect(self._on_screen_removed)
        self.setAccessibleName("Orpheus status: Idle")
        self.setAccessibleDescription("Dictation status")

    # -- public slots ---------------------------------------------------------

    @Slot(str)
    def set_state(self, state: str) -> None:
        if self._motion_override is None:
            self._reduced_motion = not _client_area_animations_enabled()
            if self._reduced_motion:
                self._anim.stop()
                self._appear_t = _APPEAR_S
                self.setWindowOpacity(1.0)
        if state == "processing":
            state = "transcribing"
        if state not in _LABELS and state != "idle":
            raise ValueError(f"Unknown pill state: {state}")
        if state == "idle":
            self._hold_timer.stop()
            if self._reduced_motion:
                self._anim.stop()
                self._closing = False
                self.hide()
                self.setWindowOpacity(1.0)
                self._state = "idle"
                self._set_idle_accessibility()
            elif self.isVisible() and not self._closing:
                self._begin_close()
            return

        was_hidden = not self.isVisible() or self._closing
        previous = self._state
        self._state = state
        self._resize_for_state()
        self._reposition()
        self._t = 0.0
        self._closing = False
        self._close_t = 0.0
        self._hold_timer.stop()
        if state == "listening":
            self._levels.clear()
            self._levels.extend([0.0] * _BAR_COUNT)
            self._shown = [0.0] * _BAR_COUNT
            self._normalizer = LevelNormalizer()
        if state in {"done", "cancelled"}:
            delay = _DONE_HOLD_MS if state == "done" else _CANCELLED_HOLD_MS
            self._hold_timer.start(delay)
        if was_hidden:
            self._appear_t = 0.0 if not self._reduced_motion else _APPEAR_S
            self.setWindowOpacity(1.0 if self._reduced_motion else 0.0)
            self._reposition()
            self.show()
        if not self._reduced_motion and not self._anim.isActive():
            self._anim.start()
        elif self._reduced_motion:
            self._anim.stop()
        self._publish_accessible_state(previous != state)
        self.update()

    @Slot(float)
    def set_level(self, value: float) -> None:
        normalized = self._normalizer.level(max(0.0, float(value)))
        self._levels.append(normalized)
        if self._reduced_motion and self._state == "listening":
            self._shown = list(self._levels)
            self.update()

    @Slot(str)
    def set_message(self, text: str) -> None:
        self._message = text
        if self._state == "error":
            description = self._error_description()
            self.setToolTip(description)
            self.setAccessibleDescription(description)
            QAccessible.updateAccessibility(
                QAccessibleEvent(self, QAccessible.Event.DescriptionChanged)
            )
        self.update()

    def set_target_screen(self, screen: QScreen | None) -> None:
        """Place the pill on the requested monitor, or the primary fallback."""
        if self._target_screen is not None and self._screen_geometry_slot is not None:
            try:
                self._target_screen.availableGeometryChanged.disconnect(
                    self._screen_geometry_slot
                )
            except (AttributeError, RuntimeError, TypeError):
                pass
        screens = QGuiApplication.screens()
        selected = screen if screen is not None and screen in screens else None
        if selected is None:
            selected = QGuiApplication.primaryScreen()
        self._target_screen = selected
        self._screen_geometry_slot = None
        if selected is not None:
            try:
                self._screen_geometry_slot = self._on_available_geometry_changed
                selected.availableGeometryChanged.connect(self._screen_geometry_slot)
            except (AttributeError, RuntimeError, TypeError):
                self._screen_geometry_slot = None
        self._reposition()

    # -- accessible status and placement -------------------------------------

    def _error_description(self) -> str:
        details = self._message.strip() or "An error needs attention."
        return f"{details} Open Recover text from the tray for details and recovery."

    def _publish_accessible_state(self, announce: bool) -> None:
        label = _LABELS[self._state]
        self.setAccessibleName(f"Orpheus status: {label}")
        description = (self._error_description() if self._state == "error"
                       else f"Dictation status: {label}.")
        self.setAccessibleDescription(description)
        if self._state == "error":
            self.setToolTip(description)
        else:
            self.setToolTip("")
        if announce:
            QAccessible.updateAccessibility(
                QAccessibleEvent(self, QAccessible.Event.NameChanged)
            )
            if QAccessibleAnnouncementEvent is not None:
                event = QAccessibleAnnouncementEvent(self, label)
                event.setPoliteness(
                    QAccessible.AnnouncementPoliteness.Assertive
                    if self._state == "error"
                    else QAccessible.AnnouncementPoliteness.Polite
                )
                QAccessible.updateAccessibility(event)

    def _on_screen_removed(self, screen: QScreen) -> None:
        if screen is self._target_screen or screen == self._target_screen:
            self.set_target_screen(None)

    def _on_available_geometry_changed(self, *args) -> None:
        self._reposition()

    def _reposition(self) -> None:
        screen = self._target_screen
        available = QGuiApplication.screens()
        if screen is None or screen not in available:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        desired_x = geo.left() + (geo.width() - self.width()) // 2
        desired_y = geo.bottom() - self.height() - 16 + 1
        max_x = max(geo.left(), geo.right() - self.width() + 1)
        max_y = max(geo.top(), geo.bottom() - self.height() + 1)
        x = min(max(geo.left(), desired_x), max_x)
        y = min(max(geo.top(), desired_y), max_y)
        self.move(x, y)

    # -- animation ------------------------------------------------------------

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
        self._set_idle_accessibility()

    def _set_idle_accessibility(self) -> None:
        self.setAccessibleName("Orpheus status: Idle")
        self.setAccessibleDescription("Dictation status")
        QAccessible.updateAccessibility(
            QAccessibleEvent(self, QAccessible.Event.NameChanged)
        )

    def _opacity(self) -> float:
        appear = _ease_out(self._appear_t / _APPEAR_S)
        close = _ease_out(self._close_t / _CLOSE_S) if self._closing else 0.0
        return appear * (1.0 - close)

    def _tick(self) -> None:
        if self._reduced_motion:
            self._anim.stop()
            return
        dt = _TICK_MS / 1000.0
        self._t += dt
        if self._appear_t < _APPEAR_S:
            self._appear_t = min(_APPEAR_S, self._appear_t + dt)
        for i, target in enumerate(self._levels):
            k = 0.55 if target > self._shown[i] else 0.25
            self._shown[i] += (target - self._shown[i]) * k
        if self._closing:
            self._close_t += dt
            if self._close_t >= _CLOSE_S:
                self._finish_close()
                return
        self.setWindowOpacity(self._opacity())
        self.update()
        if (not self._closing and self._appear_t >= _APPEAR_S
                and self._state not in {
                    "listening", "transcribing", "cleaning", "delivering"
                }):
            self._anim.stop()

    # -- painting --------------------------------------------------------------

    def _label_font(self):
        font = self.font()
        font.setPointSizeF(9.0)
        font.setWeight(font.Weight.DemiBold)
        return font

    def _resize_for_state(self) -> None:
        metrics = QFontMetricsF(self._label_font())
        # Keep normal status widths stable, using Transcribing as the baseline.
        # Longer exceptional labels still need enough room to remain readable.
        label_width = max(metrics.horizontalAdvance("Transcribing"),
                          metrics.horizontalAdvance(self._display_text()))
        padding = 36 if self._state == "error" else 24
        width = 88 if self._state == "listening" else math.ceil(label_width) + padding
        self.setFixedSize(width, max(30, math.ceil(metrics.height()) + 12))

    def _display_text(self) -> str:
        return "" if self._state in {"idle", "listening"} else _LABELS.get(self._state, "")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._reduced_motion:
            appear = _ease_out(self._appear_t / _APPEAR_S)
            close = _ease_out(self._close_t / _CLOSE_S) if self._closing else 0.0
            painter.translate(0, (1.0 - appear) * _SLIDE_PX + close * _SLIDE_PX)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(_SURFACE)
        painter.setPen(QPen(_EDGE, 1))
        radius = rect.height() / 2
        painter.drawRoundedRect(rect, radius, radius)
        painter.setFont(self._label_font())
        painter.setPen(_TEXT)
        label = self._display_text()

        if self._state == "listening":
            self._paint_bars(painter, rect.adjusted(12, 0, -12, 0))
        elif self._state == "error":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_ERROR_ACCENT)
            painter.drawEllipse(QPointF(rect.left() + 15, rect.center().y()), 3.5, 3.5)
            painter.setPen(_TEXT)
            painter.drawText(rect.adjusted(27, 0, -8, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             label)
        elif self._state == "done":
            painter.setPen(_ACCENT)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        else:
            if self._state == "transcribing" and not self._reduced_motion:
                center = -24 + (self._t % 2.4) / 2.4 * (rect.width() + 48)
                glow = QLinearGradient(center - 24, 0, center + 24, 0)
                glow.setColorAt(0, QColor(180, 190, 185))
                glow.setColorAt(0.5, _TEXT)
                glow.setColorAt(1, QColor(180, 190, 185))
                painter.setPen(QPen(QBrush(glow), 1))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_bars(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_TEXT)
        pitch = rect.width() / _BAR_COUNT
        mid_y = rect.center().y()
        max_h = rect.height() - 14
        for i, level in enumerate(self._shown):
            height = max(2.0, level * max_h)
            painter.drawRoundedRect(
                QRectF(rect.left() + i * pitch + (pitch - 2.2) / 2,
                       mid_y - height / 2, 2.2, height), 1.1, 1.1
            )
