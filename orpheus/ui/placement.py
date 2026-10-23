"""Resolve the target monitor by its native handle, independent of display names."""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtGui import QGuiApplication


def _monitor_handle(hwnd: int) -> int:
    if sys.platform != 'win32' or not hwnd:
        return 0
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    return int(user32.MonitorFromWindow(hwnd, 2) or 0)


def screen_for_target(target):
    handle = _monitor_handle(target.hwnd) if target is not None else 0
    if handle:
        for screen in QGuiApplication.screens():
            try:
                native = screen.nativeInterface()
                if native is not None and int(native.handle()) == handle:
                    return screen
            except (AttributeError, RuntimeError, TypeError):
                continue  # a removed screen or a non-Windows platform interface
    return QGuiApplication.primaryScreen()
