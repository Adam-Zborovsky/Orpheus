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


def test_failed_rebind_restores_previous_listener(monkeypatch):
    import orpheus.hotkey as module

    starts = []

    class Listener:
        def __init__(self, bindings):
            self.key = next(iter(bindings))

        def start(self):
            starts.append(self.key)
            if self.key == "<f10>":
                raise RuntimeError("registration failed")

        def stop(self):
            pass

    monkeypatch.setattr(module.keyboard, "GlobalHotKeys", Listener)
    manager = HotkeyManager("<f9>", lambda: None)
    manager.start()
    with pytest.raises(RuntimeError, match="previous shortcut is still active"):
        manager.rebind("<f10>")
    assert manager.hotkey == "<f9>"
    assert starts == ["<f9>", "<f10>", "<f9>"]
    assert manager._listener.key == "<f9>"
