"""Entry point wiring tests with no OS, audio, model, or live-data access."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from orpheus import __main__ as entrypoint
from orpheus.app import State
from orpheus.settings import Settings, SettingsError


class FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def disconnect(self, slot):
        self._slots.remove(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


def make_entry_fakes(monkeypatch, tmp_path, *, invalid_config=False,
                     history_file=False, scenario=None):
    events = []
    refs = {}
    config_path = tmp_path / "config.toml"
    config_path.write_bytes(b"invalid = [ original bytes\n" if invalid_config else b"valid = true\n")
    if history_file:
        (tmp_path / "history.sqlite3").write_bytes(b"temporary fake history marker")

    class FakeApplication:
        def __init__(self, argv):
            refs["app"] = self
            events.append("app:init")

        def setApplicationName(self, name):
            events.append(f"app:name:{name}")

        def setWindowIcon(self, icon):
            events.append("app:icon")

        def setQuitOnLastWindowClosed(self, value):
            events.append(f"app:last-window:{value}")

        def exec(self):
            events.append("app:exec")
            if scenario is not None:
                scenario(refs)
            return 7

        def quit(self):
            events.append("app:quit")

    class FakeMessageBox:
        @staticmethod
        def critical(parent, title, text):
            events.append(("message:critical", title, text))

        @staticmethod
        def warning(parent, title, text):
            events.append(("message:warning", title, text))

    class FakeSettingsWindow:
        def __init__(self, settings):
            self.settings = settings
            self.saved = FakeSignal()
            self.reload_model_requested = FakeSignal()
            self.history_requested = FakeSignal()
            self.runtime_statuses = []
            self._save_handler = None
            refs.setdefault("settings_windows", []).append(self)
            events.append(("settings:init", settings))

        def set_save_handler(self, handler):
            self._save_handler = handler

        def set_runtime_status(self, status):
            self.runtime_statuses.append(status)

        def exec(self):
            events.append("settings:exec")
            if invalid_config:
                return 0  # user cancels repair; no saved signal
            self.reload_model_requested.emit()
            refs["controller"].backend_changed.emit("backend during dialog")
            self.history_requested.emit()
            return 0

        def deleteLater(self):
            events.append("settings:delete")

    class FakeHistoryStore:
        def __init__(self, path):
            self.path = path
            self.closed = False
            refs.setdefault("stores", []).append(self)
            events.append(("history:open", path))

        def close(self):
            self.closed = True
            events.append("history:close")

    class FakeHistoryWindow:
        def __init__(self, entries=None, parent=None, *, store=None):
            self.store = store
            refs.setdefault("history_windows", []).append(self)
            events.append(("history_window:init", store))

        def exec(self):
            events.append("history_window:exec")
            if self.store is not None:
                assert not self.store.closed

        def deleteLater(self):
            events.append("history_window:delete")

    class FakePill:
        def __init__(self):
            self.states = []
            self.levels = []
            self.messages = []
            self.screens = []
            refs["pill"] = self
            events.append("pill:init")

        def set_state(self, value):
            self.states.append(value)

        def set_level(self, value):
            self.levels.append(value)

        def set_message(self, value):
            self.messages.append(value)

        def set_target_screen(self, screen):
            self.screens.append(screen)

    class FakeTray:
        def __init__(self):
            self.settings_requested = FakeSignal()
            self.history_requested = FakeSignal()
            self.recovery_requested = FakeSignal()
            self.cancel_retry_requested = FakeSignal()
            self.quit_requested = FakeSignal()
            self.notices = []
            refs["tray"] = self
            events.append("tray:init")

        def set_status(self, value):
            events.append(("tray:status", value))

        def set_recovery_count(self, value):
            events.append(("tray:recovery-count", value))

        def show_notice(self, value):
            self.notices.append(value)

        def show_error(self, value):
            events.append(("tray:error", value))

        def show(self):
            events.append("tray:show")

    class FakeRecoveryWindow:
        def __init__(self, controller):
            self.settings_requested = FakeSignal()
            events.append("recovery:init")

        def show(self):
            events.append("recovery:show")

    class FakeController:
        def __init__(self, settings):
            self.settings = settings
            self.state_changed = FakeSignal()
            self.level_changed = FakeSignal()
            self.error = FakeSignal()
            self.notice = FakeSignal()
            self.status_changed = FakeSignal()
            self.backend_changed = FakeSignal()
            self.target_changed = FakeSignal()
            self.recovery_changed = FakeSignal()
            self.settings_failed = FakeSignal()
            self.shutdown_finished = FakeSignal()
            self.settings_apply_hook = None
            self.status = "Loading model"
            self.backend_status = "CPU · float32 · cached model"
            self.pending_settings = False
            self.recoveries = []
            self._machine = SimpleNamespace(state=State.IDLE)
            refs["controller"] = self
            events.append("controller:init")

        def preload(self):
            events.append("controller:preload")

        def toggle(self):
            events.append("controller:toggle")

        def cancel_retry(self):
            events.append("controller:cancel-retry")

        def apply_settings(self, settings):
            events.append(("controller:apply-settings", settings))

        def shutdown(self):
            events.append("controller:shutdown")
            events.append("controller:shutdown-finished")
            self.shutdown_finished.emit()

        def _fail(self, message):
            events.append(("controller:fail", message))

    class FakeHotkeys:
        def __init__(self, hotkey, callback):
            self.callback = callback
            refs["hotkeys"] = self
            events.append(("hotkeys:init", hotkey))

        def start(self):
            events.append("hotkeys:start")

        def stop(self):
            events.append("hotkeys:stop")

        def rebind(self, hotkey):
            events.append(("hotkeys:rebind", hotkey))

    monkeypatch.setattr(entrypoint, "QApplication", FakeApplication)
    monkeypatch.setattr(entrypoint, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(entrypoint, "QSystemTrayIcon",
                        SimpleNamespace(isSystemTrayAvailable=staticmethod(lambda: True)))
    monkeypatch.setattr(entrypoint, "AppController", FakeController)
    monkeypatch.setattr(entrypoint, "HotkeyManager", FakeHotkeys)
    monkeypatch.setattr(entrypoint, "PillOverlay", FakePill)
    monkeypatch.setattr(entrypoint, "TrayIcon", FakeTray)
    monkeypatch.setattr(entrypoint, "RecoveryWindow", FakeRecoveryWindow)
    monkeypatch.setattr(entrypoint, "SettingsWindow", FakeSettingsWindow)
    monkeypatch.setattr(entrypoint, "HistoryWindow", FakeHistoryWindow)
    monkeypatch.setattr(entrypoint, "HistoryStore", FakeHistoryStore)
    monkeypatch.setattr(entrypoint, "app_icon", lambda: object())
    monkeypatch.setattr(entrypoint, "screen_for_target", lambda target: "fake-screen")
    monkeypatch.setattr(entrypoint, "configure_logging", lambda path: events.append("logging:configure"))
    monkeypatch.setattr(entrypoint, "record_event", lambda *args, **kwargs: events.append(("diagnostic", *args)))
    monkeypatch.setattr(entrypoint.logging, "basicConfig", lambda **kwargs: events.append("logging:basic"))
    monkeypatch.setattr(entrypoint, "default_config_path", lambda: config_path)
    if invalid_config:
        monkeypatch.setattr(entrypoint, "load_settings",
                            lambda path: (_ for _ in ()).throw(SettingsError("config malformed")))
    else:
        monkeypatch.setattr(entrypoint, "load_settings", lambda path: Settings())
    monkeypatch.setattr(entrypoint, "save_settings",
                        lambda path, settings: events.append(("settings:save", path, settings)))
    monkeypatch.setattr(entrypoint.sys, "platform", "test-platform")
    return refs, events, config_path


def test_valid_startup_preloads_then_shuts_down_after_hotkey_stops(
        monkeypatch, tmp_path):
    def drive_app(refs):
        refs["controller"].target_changed.emit(object())
        refs["tray"].settings_requested.emit()
        refs["controller"].backend_changed.emit("after dialog")
        refs["tray"].quit_requested.emit()

    refs, events, _config = make_entry_fakes(
        monkeypatch, tmp_path, history_file=True, scenario=drive_app
    )
    result = entrypoint.main()

    assert result == 7
    preload_positions = [i for i, event in enumerate(events)
                         if event == "controller:preload"]
    assert events.index("hotkeys:start") < preload_positions[0]
    assert preload_positions[0] < events.index("app:exec") < preload_positions[1]
    first_stop = events.index("hotkeys:stop")
    shutdown = events.index("controller:shutdown")
    finished = events.index("controller:shutdown-finished")
    assert first_stop < shutdown < finished < events.index("app:quit")
    assert refs["tray"].notices == []  # CPU-ready startup is silent
    assert refs["pill"].screens == ["fake-screen"]
    assert len(preload_positions) == 2  # startup load and Settings reload wiring
    assert len(refs["stores"]) == 1 and refs["stores"][0].closed
    assert events.index("history_window:exec") < events.index("history:close")
    assert all(window.runtime_statuses for window in refs["settings_windows"])
    assert "backend during dialog" in refs["settings_windows"][0].runtime_statuses
    assert "after dialog" not in refs["settings_windows"][0].runtime_statuses


def test_invalid_config_repair_cancel_leaves_bytes_and_stops_before_runtime(
        monkeypatch, tmp_path):
    refs, events, config_path = make_entry_fakes(
        monkeypatch, tmp_path, invalid_config=True
    )
    original = config_path.read_bytes()

    result = entrypoint.main()

    assert result == 1
    assert config_path.read_bytes() == original
    assert refs["settings_windows"][0].settings.history_enabled is False
    assert refs["settings_windows"][0].settings.cleanup_enabled is False
    assert "controller:init" not in events
    assert not any(isinstance(event, tuple) and event[0] == "hotkeys:init"
                   for event in events)
    assert "tray:init" not in events
    assert "hotkeys:start" not in events
    assert "app:exec" not in events
    assert any(isinstance(event, tuple) and event[0] == "message:critical"
               for event in events)


def test_missing_tray_exits_before_config_or_runtime(monkeypatch, tmp_path):
    refs, events, _config = make_entry_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(entrypoint, "QSystemTrayIcon",
                        SimpleNamespace(isSystemTrayAvailable=staticmethod(lambda: False)))

    assert entrypoint.main() == 1
    assert "message:critical" in [event[0] for event in events if isinstance(event, tuple)]
    assert "controller:init" not in events
    assert "hotkeys:start" not in events
    assert "logging:configure" not in events


def test_deferred_rejection_restores_active_config_for_next_launch(monkeypatch, tmp_path):
    active = Settings(hotkey="<f9>")

    def scenario(refs):
        refs["controller"].settings_failed.emit(active, "registration failed")
        refs["tray"].settings_requested.emit()

    refs, events, path = make_entry_fakes(monkeypatch, tmp_path, scenario=scenario)
    assert entrypoint.main() == 7
    assert ("settings:save", path, active) in events
    assert refs["settings_windows"][0].settings.hotkey == "<f9>"


def test_hotkey_start_failure_keeps_settings_reachable(monkeypatch, tmp_path):
    def scenario(refs):
        refs["tray"].settings_requested.emit()

    refs, events, _path = make_entry_fakes(monkeypatch, tmp_path, scenario=scenario)
    monkeypatch.setattr(entrypoint.HotkeyManager, "start", lambda self: (_ for _ in ()).throw(RuntimeError("cannot start")))
    assert entrypoint.main() == 7
    assert "app:exec" in events
    assert "settings:exec" in events
    assert any(isinstance(event, tuple) and event[0] == "controller:fail" for event in events)
