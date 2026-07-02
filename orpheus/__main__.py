"""Orpheus entry point: python -m orpheus"""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .app import AppController
from .hotkey import HotkeyManager
from .settings import default_config_path, load_settings, save_settings
from .ui.pill import PillOverlay
from .ui.settings_window import SettingsWindow
from .ui.tray import TrayIcon


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("Orpheus")
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
        dialog.exec()

    tray.settings_requested.connect(open_settings)
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
