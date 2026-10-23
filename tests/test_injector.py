import sys
import types

import pytest

import orpheus.injector as injector
from orpheus.injector import (DeliveryError, ForegroundTarget, TextInjector,
                              normalize_newlines, utf16_units)


def test_utf16_units_ascii():
    assert utf16_units("Hi") == [0x48, 0x69]


def test_utf16_units_hebrew():
    # שלום — each Hebrew letter is a single BMP code unit
    assert utf16_units("שלום") == [0x05E9, 0x05DC, 0x05D5, 0x05DD]


def test_utf16_units_emoji_surrogate_pair():
    # U+1F600 GRINNING FACE encodes as a surrogate pair
    assert utf16_units("\U0001F600") == [0xD83D, 0xDE00]


def test_normalize_newlines():
    assert normalize_newlines("a\r\nb\nc") == "a\rb\rc"


def _fake_keyboard(monkeypatch, send):
    monkeypatch.setattr(injector.sys, "platform", "win32")
    monkeypatch.setattr(injector, "_key_input",
                        lambda code, flags, unicode=True: (code, flags, unicode),
                        raising=False)
    monkeypatch.setattr(injector, "_send_inputs", send, raising=False)
    monkeypatch.setattr(injector.time, "sleep", lambda _delay: None)


def test_foreground_target_is_frozen_value():
    target = ForegroundTarget(hwnd=123, pid=456)
    assert target == ForegroundTarget(123, 456)
    with pytest.raises(AttributeError):
        target.pid = 789


def test_type_sends_unicode_in_chunks_and_preserves_astral_code_units(monkeypatch):
    batches = []
    _fake_keyboard(monkeypatch, lambda inputs: batches.append(inputs) or len(inputs))
    injector.TextInjector(chunk_chars=1, chunk_delay_s=0)._type("A😀")
    assert [len(batch) for batch in batches] == [2, 2, 2]
    assert batches[0][0][0] == 0x41
    assert batches[1][0][0] == 0xD83D
    assert batches[2][0][0] == 0xDE00


def test_target_mismatch_or_missing_snapshot_sends_no_input(monkeypatch):
    calls = []
    _fake_keyboard(monkeypatch, lambda inputs: calls.append(inputs) or len(inputs))
    target = ForegroundTarget(1, 2)
    monkeypatch.setattr(injector, "_get_foreground_target", lambda: None)
    with pytest.raises(DeliveryError, match="target changed"):
        injector.TextInjector()._type("private text", target, lambda: False)
    assert calls == []


def test_target_pid_change_stops_before_next_chunk(monkeypatch):
    calls = []
    _fake_keyboard(monkeypatch, lambda inputs: calls.append(inputs) or len(inputs))
    target = ForegroundTarget(1, 2)
    current = iter([target, ForegroundTarget(1, 99)])
    monkeypatch.setattr(injector, "_get_foreground_target", lambda: next(current))
    with pytest.raises(DeliveryError) as error:
        injector.TextInjector(chunk_chars=1)._type("ab", target, lambda: False)
    assert error.value.sent_units == 1
    assert error.value.partial is True
    assert calls and len(calls) == 1


def test_cancellation_before_first_chunk_sends_nothing(monkeypatch):
    calls = []
    _fake_keyboard(monkeypatch, lambda inputs: calls.append(inputs) or len(inputs))
    with pytest.raises(DeliveryError) as error:
        injector.TextInjector()._type("text", is_cancelled=lambda: True)
    assert error.value.cancelled is True
    assert error.value.sent_units == 0
    assert calls == []


def test_cancellation_after_chunk_reports_delivered_units(monkeypatch):
    calls = []
    _fake_keyboard(monkeypatch, lambda inputs: calls.append(inputs) or len(inputs))
    checks = iter([False, True])
    with pytest.raises(DeliveryError) as error:
        injector.TextInjector(chunk_chars=1)._type(
            "ab", is_cancelled=lambda: next(checks),
        )
    assert error.value.cancelled is True
    assert error.value.sent_units == 1
    assert error.value.partial is True
    assert len(calls) == 1


def test_short_sendinput_reports_confirmed_units_and_partial_delivery(monkeypatch):
    _fake_keyboard(monkeypatch, lambda inputs: 3)
    with pytest.raises(DeliveryError) as error:
        injector.TextInjector(chunk_chars=2)._type("ab")
    assert error.value.sent_units == 1
    assert error.value.partial is True
    assert "ab" not in str(error.value)


def _fake_clipboard(monkeypatch, *, target, user_change_on_send=False):
    state = {"text": "prior text", "sequence": 10, "copy_calls": []}

    class Clipboard:
        @staticmethod
        def paste():
            return state["text"]

        @staticmethod
        def copy(text):
            state["text"] = text
            state["copy_calls"].append(text)
            state["sequence"] += 1

    monkeypatch.setitem(sys.modules, "pyperclip", Clipboard)
    monkeypatch.setattr(injector, "_clipboard_sequence_number",
                        lambda: state["sequence"])
    monkeypatch.setattr(injector, "_clipboard_has_unsupported_formats", lambda: False)
    monkeypatch.setattr(injector, "_get_foreground_target", lambda: target)
    monkeypatch.setattr(injector.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(injector, "_key_input",
                        lambda code, flags, unicode=True: (code, flags, unicode),
                        raising=False)

    def send(inputs):
        if user_change_on_send:
            state["text"] = "new user copy"
            state["sequence"] += 1
        return len(inputs)

    monkeypatch.setattr(injector, "_send_inputs", send, raising=False)
    return state


def test_paste_restores_prior_text_only_if_ours_is_still_on_clipboard(monkeypatch):
    target = ForegroundTarget(10, 20)
    state = _fake_clipboard(monkeypatch, target=target)
    injector.TextInjector("paste")._paste("secret transcript", target, lambda: False)
    assert state["text"] == "prior text"
    assert state["copy_calls"] == ["secret transcript", "prior text"]


def test_paste_does_not_overwrite_newer_user_clipboard(monkeypatch):
    target = ForegroundTarget(10, 20)
    state = _fake_clipboard(monkeypatch, target=target, user_change_on_send=True)
    injector.TextInjector("paste")._paste("secret transcript", target, lambda: False)
    assert state["text"] == "new user copy"
    assert state["copy_calls"] == ["secret transcript"]


def test_paste_refuses_nontext_clipboard_before_copy(monkeypatch):
    target = ForegroundTarget(10, 20)
    state = _fake_clipboard(monkeypatch, target=target)
    monkeypatch.setattr(injector, "_clipboard_has_unsupported_formats", lambda: True)
    with pytest.raises(DeliveryError, match="non-text"):
        injector.TextInjector("paste")._paste("text", target, lambda: False)
    assert state["copy_calls"] == []


def test_paste_target_change_aborts_ctrlv_and_restores_clipboard(monkeypatch):
    target = ForegroundTarget(10, 20)
    state = _fake_clipboard(monkeypatch, target=target)
    calls = []
    monkeypatch.setattr(injector, "_send_inputs",
                        lambda inputs: calls.append(inputs) or len(inputs))
    current = iter([target, ForegroundTarget(11, 21)])
    monkeypatch.setattr(injector, "_get_foreground_target", lambda: next(current))
    with pytest.raises(DeliveryError, match="target changed"):
        injector.TextInjector("paste")._paste("text", target, lambda: False)
    assert calls == []
    assert state["text"] == "prior text"


def test_paste_partial_send_best_effort_releases_keys_without_masking(monkeypatch):
    target = ForegroundTarget(10, 20)
    _fake_clipboard(monkeypatch, target=target)
    batches = []

    def send(inputs):
        batches.append(inputs)
        if len(batches) == 1:
            return 1
        raise OSError("release failed")

    monkeypatch.setattr(injector, "_send_inputs", send)
    with pytest.raises(DeliveryError) as error:
        injector.TextInjector("paste")._paste("text", target, lambda: False)
    assert error.value.partial is True
    assert len(batches) == 2
