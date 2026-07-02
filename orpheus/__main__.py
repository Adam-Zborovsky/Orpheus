"""Orpheus entry point: python -m orpheus"""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .app import AppController
from .history import HistoryStore
from .hotkey import HotkeyManager
from .settings import default_config_path, load_settings, save_settings
from .ui.history_window import HistoryWindow
from .ui.pill import PillOverlay
from .ui.settings_window import SettingsWindow
from .ui.tray import TrayIcon, app_icon


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if sys.platform == "win32":
        # Give the process its own taskbar identity; otherwise Windows groups
        # every window under the generic python.exe icon.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Zborovsky.Orpheus")
    app = QApplication(sys.argv)
    app.setApplicationName("Orpheus")
    app.setWindowIcon(app_icon())  # taskbar + window icon (settings dialog)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Orpheus", "System tray is not available.")
        return 1

    config_path = default_config_path()
    state = {"settings": load_settings(config_path)}

    controller = AppController(state["settings"])
    pill = PillOverlay()
    tray = TrayIcon()

    controller.state_changed.connect(pill.set_state)
    controller.level_changed.connect(pill.set_level)
    controller.error.connect(pill.set_message)
    controller.error.connect(tray.show_notice)  # pill is small; full text via tray
    controller.notice.connect(tray.show_notice)

    hotkeys = HotkeyManager(state["settings"].hotkey, controller.toggle)

    def on_settings_saved(new_settings) -> None:
        save_settings(config_path, new_settings)
        controller.apply_settings(new_settings)
        hotkeys.rebind(new_settings.hotkey)
        state["settings"] = new_settings

    def open_settings() -> None:
        dialog = SettingsWindow(state["settings"])
        dialog.saved.connect(on_settings_saved)
        dialog.history_requested.connect(open_history)
        dialog.exec()

    def open_history() -> None:
        # Short-lived read, independent of AppController's own history
        # connection — works whether or not history is currently enabled.
        history_path = config_path.parent / "history.sqlite3"
        entries = []
        if history_path.exists():
            store = HistoryStore(history_path)
            try:
                entries = store.recent(limit=200)
            finally:
                store.close()
        HistoryWindow(entries).exec()

    tray.settings_requested.connect(open_settings)
    tray.history_requested.connect(open_history)
    tray.quit_requested.connect(app.quit)
    tray.show()

    hotkeys.start()
    controller.preload()

    exit_code = app.exec()
    hotkeys.stop()
    controller.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
