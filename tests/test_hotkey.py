import pytest

from orpheus.hotkey import HotkeyManager, validate_hotkey


def test_validate_accepts_default():
    assert validate_hotkey("<ctrl>+<alt>+<space>") is True


def test_validate_accepts_char_combo():
    assert validate_hotkey("<ctrl>+<shift>+d") is True


def test_validate_rejects_garbage():
    assert validate_hotkey("notakey") is False
    assert validate_hotkey("<ctrl>+") is False


def test_constructor_rejects_invalid():
    with pytest.raises(ValueError):
        HotkeyManager("garbage+string", on_toggle=lambda: None)


def test_rebind_invalid_raises_and_keeps_old():
    manager = HotkeyManager("<f9>", on_toggle=lambda: None)
    with pytest.raises(ValueError):
        manager.rebind("nope nope")
    assert manager.hotkey == "<f9>"


def test_rebind_valid_updates():
    manager = HotkeyManager("<f9>", on_toggle=lambda: None)
    manager.rebind("<ctrl>+<alt>+<space>")
    assert manager.hotkey == "<ctrl>+<alt>+<space>"
