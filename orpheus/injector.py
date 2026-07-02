"""Type text into the focused window via SendInput; clipboard-paste fallback."""
from __future__ import annotations

import ctypes
import sys
import time

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_V = 0x56


def utf16_units(text: str) -> list[int]:
    """UTF-16 code units for text; astral chars become surrogate pairs."""
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def normalize_newlines(text: str) -> str:
    """SendInput Unicode expects carriage return for the Enter key."""
    return text.replace("\r\n", "\n").replace("\n", "\r")


if sys.platform == "win32":
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class _INPUTUNION(ctypes.Union):
        # MOUSEINPUT (32 bytes on x64) is the largest union member; pad to match
        _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_ubyte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]

    def _key_input(code: int, flags: int, unicode: bool = True) -> INPUT:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        if unicode:
            inp.union.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | flags, 0, 0)
        else:
            inp.union.ki = KEYBDINPUT(code, 0, flags, 0, 0)
        return inp

    def _send_inputs(inputs: list) -> None:
        array = (INPUT * len(inputs))(*inputs)
        sent = ctypes.windll.user32.SendInput(len(array), array,
                                              ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise RuntimeError(
                "SendInput was blocked (target window may be elevated)")


class TextInjector:
    """Delivers final text to the focused app: type-out (default) or paste."""

    def __init__(self, delivery: str = "type", chunk_chars: int = 32,
                 chunk_delay_s: float = 0.005):
        self.delivery = delivery
        self.chunk_chars = chunk_chars
        self.chunk_delay_s = chunk_delay_s

    def inject(self, text: str) -> None:
        if not text:
            return
        if sys.platform != "win32":
            raise RuntimeError("text injection is only implemented for Windows")
        if self.delivery == "paste":
            self._paste(text)
        else:
            self._type(text)

    def _type(self, text: str) -> None:
        units = utf16_units(normalize_newlines(text))
        inputs = []
        for unit in units:
            inputs.append(_key_input(unit, 0))
            inputs.append(_key_input(unit, KEYEVENTF_KEYUP))
        step = self.chunk_chars * 2  # down+up per unit
        for i in range(0, len(inputs), step):
            _send_inputs(inputs[i:i + step])
            time.sleep(self.chunk_delay_s)

    def _paste(self, text: str) -> None:
        import pyperclip

        previous = pyperclip.paste()
        pyperclip.copy(text)
        time.sleep(0.05)  # let the clipboard settle before Ctrl+V
        _send_inputs([
            _key_input(VK_CONTROL, 0, unicode=False),
            _key_input(VK_V, 0, unicode=False),
            _key_input(VK_V, KEYEVENTF_KEYUP, unicode=False),
            _key_input(VK_CONTROL, KEYEVENTF_KEYUP, unicode=False),
        ])
        time.sleep(0.3)  # give the target app time to read the clipboard
        pyperclip.copy(previous)
