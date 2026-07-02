"""Global toggle hotkey via pynput."""
from __future__ import annotations

from typing import Callable

from pynput import keyboard


def validate_hotkey(hotkey: str) -> bool:
    try:
        keyboard.HotKey.parse(hotkey)
        return True
    except ValueError:
        return False


class HotkeyManager:
    """Owns a pynput GlobalHotKeys listener bound to a single toggle combo.

    The on_toggle callback fires on the listener thread, not the UI thread.
    """

    def __init__(self, hotkey: str, on_toggle: Callable[[], None]):
        if not validate_hotkey(hotkey):
            raise ValueError(f"invalid hotkey: {hotkey!r}")
        self.hotkey = hotkey
        self._on_toggle = on_toggle
        self._listener: keyboard.GlobalHotKeys | None = None

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.GlobalHotKeys({self.hotkey: self._on_toggle})
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def rebind(self, hotkey: str) -> None:
        if not validate_hotkey(hotkey):
            raise ValueError(f"invalid hotkey: {hotkey!r}")
        was_running = self._listener is not None
        self.stop()
        self.hotkey = hotkey
        if was_running:
            self.start()
