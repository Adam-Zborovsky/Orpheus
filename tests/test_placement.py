from types import SimpleNamespace

from orpheus.ui import placement


def test_target_screen_uses_native_handle_instead_of_monitor_display_name(monkeypatch):
    primary = SimpleNamespace(nativeInterface=lambda: SimpleNamespace(handle=lambda: 1))
    secondary = SimpleNamespace(nativeInterface=lambda: SimpleNamespace(handle=lambda: 2))
    monkeypatch.setattr(placement, "_monitor_handle", lambda hwnd: 2)
    monkeypatch.setattr(placement.QGuiApplication, "screens", lambda: [primary, secondary])
    monkeypatch.setattr(placement.QGuiApplication, "primaryScreen", lambda: primary)
    assert placement.screen_for_target(SimpleNamespace(hwnd=123)) is secondary


def test_removed_monitor_falls_back_to_primary(monkeypatch):
    primary = SimpleNamespace(nativeInterface=lambda: SimpleNamespace(handle=lambda: 1))
    monkeypatch.setattr(placement, "_monitor_handle", lambda hwnd: 99)
    monkeypatch.setattr(placement.QGuiApplication, "screens", lambda: [primary])
    monkeypatch.setattr(placement.QGuiApplication, "primaryScreen", lambda: primary)
    assert placement.screen_for_target(SimpleNamespace(hwnd=123)) is primary
    assert placement.screen_for_target(None) is primary
