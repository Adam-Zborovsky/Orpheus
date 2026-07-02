"""System tray icon with settings/quit menu."""
from __future__ import annotations

from PySide6.QtCore import QRect, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def _make_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(20, 20, 24))
    painter.setBrush(QColor(120, 200, 255))
    painter.drawEllipse(QRect(8, 8, 48, 48))
    painter.setBrush(QColor(20, 20, 24))
    painter.drawRoundedRect(QRect(26, 18, 12, 22), 6, 6)  # mic capsule
    painter.drawRect(QRect(30, 40, 4, 8))                 # mic stem
    painter.end()
    return QIcon(pixmap)


class TrayIcon(QSystemTrayIcon):
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(_make_icon(), parent)
        self.setToolTip("Orpheus — voice dictation")
        menu = QMenu()
        settings_action = QAction("Settings…", menu)
        settings_action.triggered.connect(self.settings_requested.emit)
        menu.addAction(settings_action)
        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)
        self._menu = menu  # keep a reference; the tray doesn't own it
        self.setContextMenu(menu)

    def show_notice(self, message: str) -> None:
        self.showMessage("Orpheus", message,
                         QSystemTrayIcon.MessageIcon.Information, 4000)
