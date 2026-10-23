import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication

from orpheus.app import RecoveryItem
from orpheus.ui.recovery_window import RecoveryWindow


class RecoveryController(QObject):
    recovery_changed = Signal()
    status_changed = Signal(str)

    def __init__(self, partial=False, saved=False):
        super().__init__()
        self.recoveries = [RecoveryItem(1, "raw", "Complete recovered text", "Target changed",
                                        history_id=1 if saved else None, partial=partial)]
        self.last_error = "Target changed. Recover your text."
        self.status = "Ready"
        self._armed_retry = None

    def arm_retry(self, item_id):
        self._armed_retry = item_id
        self.status = "Retry armed: focus your destination and press the hotkey"
        self.recovery_changed.emit()

    def cancel_retry(self):
        self._armed_retry = None
        self.recovery_changed.emit()

    def dismiss_recovery(self, item_id):
        self.recoveries = [item for item in self.recoveries if item.id != item_id]
        self.recovery_changed.emit()

    def preload(self):
        pass


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_opening_recovery_never_writes_clipboard(qapp, monkeypatch):
    copies = []
    clipboard = type("Clipboard", (), {"setText": lambda self, text: copies.append(text)})()
    monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
    controller = RecoveryController()
    window = RecoveryWindow(controller)
    assert window._text.toPlainText() == "Complete recovered text"
    assert copies == []
    assert controller._armed_retry is None
    window._copy.click()
    assert copies == ["Complete recovered text"]


def test_partial_retry_requires_explicit_acknowledgement(qapp):
    controller = RecoveryController(partial=True)
    window = RecoveryWindow(controller)
    assert not window._retry.isEnabled()
    window._confirm.setChecked(True)
    window._retry.click()
    assert controller._armed_retry == 1
    assert "focus your destination" in window._status.text()


def test_unsaved_recovery_cannot_be_dismissed_without_confirmation(qapp):
    controller = RecoveryController()
    window = RecoveryWindow(controller)
    assert not window._dismiss.isEnabled()
    window._discard_confirm.setChecked(True)
    window._dismiss.click()
    assert controller.recoveries == []


def test_recovery_text_has_accessible_name_and_full_error(qapp):
    controller = RecoveryController()
    window = RecoveryWindow(controller)
    iface = QAccessible.queryAccessibleInterface(window._text)
    assert iface.text(QAccessible.Text.Name) == "Complete recovered transcript"
    assert "Target changed" in window._detail.text()
    assert "only in memory" in window._detail.text()
