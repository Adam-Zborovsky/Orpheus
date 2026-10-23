"""Orpheus entry point: python -m orpheus"""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .app import AppController, State
from .diagnostics import configure_logging, record_event
from .history import HistoryStore
from .hotkey import HotkeyManager
from .settings import Settings, SettingsError, default_config_path, load_settings, save_settings
from .ui.history_window import HistoryWindow
from .ui.pill import PillOverlay
from .ui.placement import screen_for_target
from .ui.recovery_window import RecoveryWindow
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
    try:
        configure_logging(config_path.parent)
    except OSError:
        QMessageBox.warning(None, "Orpheus diagnostics", "Diagnostic logs could not be opened. Check the Orpheus data folder permissions.")
    record_event("app_started")
    try:
        initial_settings = load_settings(config_path)
    except SettingsError as exc:
        record_event("settings_load_failed", error_category="config")
        QMessageBox.critical(None, "Repair Orpheus settings",
                             f"{exc}\n\nThe original file is unchanged. Correct the settings and Save to start. History and cleanup begin disabled in the repair form.")
        repair = SettingsWindow(Settings(history_enabled=False, cleanup_enabled=False))
        repaired = []
        repair.set_save_handler(lambda settings: save_settings(config_path, settings))
        repair.saved.connect(repaired.append)
        repair.exec()
        if not repaired:
            return 1
        initial_settings = repaired[0]
    state = {"settings": initial_settings, "hotkey_ready": False}

    controller = AppController(state["settings"])
    pill = PillOverlay()
    tray = TrayIcon()

    controller.state_changed.connect(pill.set_state)
    controller.level_changed.connect(pill.set_level)
    controller.error.connect(pill.set_message)
    controller.error.connect(tray.show_error)
    controller.notice.connect(tray.show_notice)
    def update_status(text: str) -> None:
        tray.set_status(text if state["hotkey_ready"] else "Hotkey unavailable · open Settings")

    controller.status_changed.connect(update_status)
    tray.set_status(controller.status)
    controller.target_changed.connect(lambda target: pill.set_target_screen(screen_for_target(target)))
    recovery_window = RecoveryWindow(controller)

    def update_recovery() -> None:
        tray.set_recovery_count(len(controller.recoveries))

    controller.recovery_changed.connect(update_recovery)
    tray.recovery_requested.connect(recovery_window.show)
    tray.cancel_retry_requested.connect(controller.cancel_retry)

    hotkeys = HotkeyManager(state["settings"].hotkey, controller.toggle)

    def on_settings_saved(new_settings) -> None:
        # Rebinding is deferred with controller resources while work is active.
        previous = state["settings"]
        save_settings(config_path, new_settings)
        try:
            controller.apply_settings(new_settings)
        except Exception:
            save_settings(config_path, previous)
            record_event("settings_save_failed", error_category="config")
            raise
        state["settings"] = new_settings

    def activate_hotkey(settings) -> None:
        hotkeys.rebind(settings.hotkey)
        hotkeys.start()  # also retries a listener that failed at startup
        state["hotkey_ready"] = True
        update_status(controller.status)

    controller.settings_apply_hook = activate_hotkey

    def restore_rejected_settings(active_settings, reason) -> None:
        state["settings"] = active_settings
        try:
            save_settings(config_path, active_settings)
        except SettingsError:
            controller._fail("The rejected settings could not be restored on disk. Open Settings and save a working shortcut before restarting.")
        record_event("settings_save_failed", error_category="config")

    controller.settings_failed.connect(restore_rejected_settings)

    def open_settings() -> None:
        dialog = SettingsWindow(state["settings"])
        dialog.set_runtime_status(controller.backend_status + (" · settings pending" if controller.pending_settings else ""))
        controller.backend_changed.connect(dialog.set_runtime_status)
        dialog.reload_model_requested.connect(controller.preload)
        dialog.set_save_handler(on_settings_saved)
        dialog.history_requested.connect(open_history)
        dialog.exec()
        controller.backend_changed.disconnect(dialog.set_runtime_status)
        dialog.deleteLater()

    recovery_window.settings_requested.connect(open_settings)

    def open_history() -> None:
        # Short-lived read, independent of AppController's own history
        # connection — works whether or not history is currently enabled.
        history_path = config_path.parent / "history.sqlite3"
        store = None
        if history_path.exists():
            try:
                store = HistoryStore(history_path)
            except Exception:
                controller._fail("History could not be opened. Check the data folder permissions; recovered text remains available in Recover text.")
                recovery_window.show()
                return
        dialog = HistoryWindow(store=store) if store is not None else HistoryWindow([])
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
            if store is not None:
                store.close()

    tray.settings_requested.connect(open_settings)
    tray.history_requested.connect(open_history)
    def quit_safely() -> None:
        unsaved = any(item.history_id is None for item in controller.recoveries)
        busy = controller._machine.state is not State.IDLE
        if unsaved or busy:
            message = "Quit will discard unsaved recovery copies." if unsaved else "Quit will cancel the current dictation."
            confirm = QMessageBox(QMessageBox.Icon.Warning, "Quit Orpheus?", message)
            discard = confirm.addButton("Quit and discard unsaved work", QMessageBox.ButtonRole.DestructiveRole)
            cancel = confirm.addButton(QMessageBox.StandardButton.Cancel)
            confirm.setDefaultButton(cancel)
            confirm.exec()
            if confirm.clickedButton() is not discard:
                return
        hotkeys.stop()
        controller.shutdown()

    tray.quit_requested.connect(quit_safely)
    controller.shutdown_finished.connect(app.quit)
    tray.show()

    try:
        hotkeys.start()
    except Exception:
        controller._fail("The global hotkey could not be started. Open Settings from the tray and save a working shortcut to retry.")
        update_status(controller.status)
    else:
        state["hotkey_ready"] = True
    controller.preload()

    exit_code = app.exec()
    hotkeys.stop()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
