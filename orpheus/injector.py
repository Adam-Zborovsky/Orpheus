"""Target-checked Unicode typing and guarded text-only clipboard paste."""
from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass
from typing import Callable

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_V = 0x56


@dataclass(frozen=True)
class ForegroundTarget:
    hwnd: int
    pid: int


class DeliveryError(RuntimeError):
    """A delivery attempt stopped; counts are UTF-16 code units."""

    def __init__(self, message: str, *, sent_units: int = 0,
                 partial: bool = False, cancelled: bool = False):
        super().__init__(message)
        self.sent_units = max(0, sent_units)
        self.partial = bool(partial)
        self.cancelled = bool(cancelled)


def utf16_units(text: str) -> list[int]:
    """UTF-16 code units for text; astral chars become surrogate pairs."""
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little")
            for i in range(0, len(data), 2)]


def normalize_newlines(text: str) -> str:
    """SendInput Unicode expects carriage return for the Enter key."""
    return text.replace("\r\n", "\n").replace("\n", "\r")


if sys.platform == "win32":
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t
    HWND = wintypes.HWND

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_ubyte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (
        wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int,
    )
    _user32.SendInput.restype = wintypes.UINT
    _user32.GetForegroundWindow.argtypes = ()
    _user32.GetForegroundWindow.restype = HWND
    _user32.GetWindowThreadProcessId.argtypes = (
        HWND, ctypes.POINTER(wintypes.DWORD),
    )
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetClipboardSequenceNumber.argtypes = ()
    _user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    _user32.OpenClipboard.argtypes = (HWND,)
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = ()
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.EnumClipboardFormats.argtypes = (wintypes.UINT,)
    _user32.EnumClipboardFormats.restype = wintypes.UINT

    def _key_input(code: int, flags: int, unicode: bool = True) -> INPUT:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        if unicode:
            inp.union.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | flags, 0, 0)
        else:
            inp.union.ki = KEYBDINPUT(code, 0, flags, 0, 0)
        return inp

    def _send_inputs(inputs: list) -> int:
        array = (INPUT * len(inputs))(*inputs)
        return int(_user32.SendInput(len(array), array, ctypes.sizeof(INPUT)))

    def _clipboard_sequence_number() -> int:
        return int(_user32.GetClipboardSequenceNumber())

    def _clipboard_has_unsupported_formats() -> bool:
        if not _user32.OpenClipboard(None):
            raise DeliveryError("Clipboard is unavailable")
        try:
            text_formats = {1, 7, 13, 16}  # ANSI, OEM, Unicode text, locale
            current = int(_user32.EnumClipboardFormats(0))
            while current:
                if current not in text_formats:
                    return True
                current = int(_user32.EnumClipboardFormats(current))
            return False
        finally:
            _user32.CloseClipboard()

else:
    def _get_foreground_target() -> ForegroundTarget | None:
        return None

    def _clipboard_sequence_number() -> int:
        return 0

    def _clipboard_has_unsupported_formats() -> bool:
        return True


def foreground_target() -> ForegroundTarget | None:
    """Return the current foreground HWND and owning PID, or None on failure."""
    if sys.platform != "win32":
        return None
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    process_id = ctypes.c_ulong(0)
    thread_id = _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if not thread_id or not process_id.value:
        return None
    return ForegroundTarget(int(hwnd), int(process_id.value))


def _get_foreground_target() -> ForegroundTarget | None:
    return foreground_target()


def _target_matches(target: ForegroundTarget | None) -> bool:
    if target is None:
        return True
    try:
        return _get_foreground_target() == target
    except Exception:
        return False


def _check_delivery(target: ForegroundTarget | None,
                    is_cancelled: Callable[[], bool], sent_units: int) -> None:
    try:
        cancelled = bool(is_cancelled())
    except Exception:
        cancelled = True
    if cancelled:
        raise DeliveryError("Delivery cancelled", sent_units=sent_units,
                            partial=sent_units > 0, cancelled=True)
    if not _target_matches(target):
        raise DeliveryError("Delivery target changed", sent_units=sent_units,
                            partial=sent_units > 0)


def _send_checked(inputs: list, *, units_per_pair: bool,
                  completed_units: int) -> tuple[int, bool]:
    """Return complete units sent and whether any input event was accepted."""
    try:
        sent_events = _send_inputs(inputs)
    except Exception as exc:
        # Do not persist or expose OS exception strings; they may contain data.
        raise DeliveryError("Keyboard input failed", sent_units=completed_units,
                            partial=True) from exc
    if sent_events != len(inputs):
        units = sent_events // 2 if units_per_pair else 0
        raise DeliveryError(
            "Keyboard input was only partially accepted",
            sent_units=completed_units + units,
            partial=completed_units > 0 or sent_events > 0,
        )
    return (len(inputs) // 2 if units_per_pair else 0, bool(sent_events))


class TextInjector:
    """Deliver text while checking cancellation and the intended foreground.

    Paste mode only supports text-only clipboard contents. It refuses to
    overwrite any non-text formats and restores the old text only if no newer
    clipboard update occurred during delivery.
    """

    def __init__(self, delivery: str = "type", chunk_chars: int = 32,
                 chunk_delay_s: float = 0.005):
        self.delivery = delivery
        self.chunk_chars = max(1, chunk_chars)
        self.chunk_delay_s = max(0.0, chunk_delay_s)

    def inject(self, text: str, *, target: ForegroundTarget | None = None,
               is_cancelled: Callable[[], bool] = lambda: False) -> None:
        if not text:
            return
        if sys.platform != "win32":
            raise DeliveryError("Text injection is only implemented for Windows")
        if self.delivery == "paste":
            self._paste(text, target, is_cancelled)
        else:
            self._type(text, target, is_cancelled)

    def _type(self, text: str, target: ForegroundTarget | None = None,
              is_cancelled: Callable[[], bool] = lambda: False) -> None:
        units = utf16_units(normalize_newlines(text))
        completed = 0
        step = self.chunk_chars
        for offset in range(0, len(units), step):
            _check_delivery(target, is_cancelled, completed)
            chunk = units[offset:offset + step]
            inputs = []
            for unit in chunk:
                inputs.append(_key_input(unit, 0))
                inputs.append(_key_input(unit, KEYEVENTF_KEYUP))
            sent, _ = _send_checked(
                inputs, units_per_pair=True, completed_units=completed,
            )
            completed += sent
            time.sleep(self.chunk_delay_s)

    def _paste(self, text: str, target: ForegroundTarget | None,
               is_cancelled: Callable[[], bool]) -> None:
        import pyperclip

        _check_delivery(target, is_cancelled, 0)
        sequence_before = _clipboard_sequence_number()
        if _clipboard_has_unsupported_formats():
            raise DeliveryError(
                "Clipboard has non-text data; paste was not attempted"
            )
        try:
            previous = pyperclip.paste()
        except Exception as exc:
            raise DeliveryError("Could not read the text clipboard") from exc
        if _clipboard_sequence_number() != sequence_before:
            raise DeliveryError("Clipboard changed before paste")

        try:
            pyperclip.copy(text)
        except Exception as exc:
            raise DeliveryError("Could not prepare text for paste") from exc
        our_sequence = _clipboard_sequence_number()
        if not our_sequence or our_sequence == sequence_before:
            raise DeliveryError("Clipboard could not be safely updated")

        primary_error: DeliveryError | None = None
        pasted = False
        try:
            time.sleep(0.05)
            _check_delivery(target, is_cancelled, 0)
            # This second check is deliberately adjacent to Ctrl+V.
            _check_delivery(target, is_cancelled, 0)
            inputs = [
                _key_input(VK_CONTROL, 0, unicode=False),
                _key_input(VK_V, 0, unicode=False),
                _key_input(VK_V, KEYEVENTF_KEYUP, unicode=False),
                _key_input(VK_CONTROL, KEYEVENTF_KEYUP, unicode=False),
            ]
            sent, _ = _send_checked(
                inputs, units_per_pair=False, completed_units=0,
            )
            del sent
            pasted = True
            time.sleep(0.3)
        except DeliveryError as exc:
            primary_error = exc
            if exc.partial:
                try:
                    _send_inputs([
                        _key_input(VK_V, KEYEVENTF_KEYUP, unicode=False),
                        _key_input(VK_CONTROL, KEYEVENTF_KEYUP, unicode=False),
                    ])
                except Exception:
                    # Best-effort key release must not mask the delivery error.
                    pass
        finally:
            # Never replace a newer user clipboard with our previous text.
            try:
                if _clipboard_sequence_number() == our_sequence:
                    pyperclip.copy(previous)
            except Exception:
                if primary_error is None:
                    primary_error = DeliveryError(
                        "Could not restore the previous text clipboard",
                        sent_units=len(utf16_units(text)) if pasted else 0,
                        partial=pasted,
                    )

        if primary_error is not None:
            raise primary_error
