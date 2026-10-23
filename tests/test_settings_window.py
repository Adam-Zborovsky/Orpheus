import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QGroupBox, QLabel

from orpheus.audio import AudioCapture
from orpheus.settings import Settings
from orpheus.ui.settings_window import SettingsWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("device", ["auto", "cpu", "cuda"])
def test_save_preserves_processing_device(qapp, monkeypatch, device):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [])
    window = SettingsWindow(Settings(device=device, num_workers=2))
    saved = []
    window.saved.connect(saved.append)
    window._language.setCurrentIndex(window._language.findData("he"))

    window._on_save()

    assert len(saved) == 1
    assert saved[0].device == device
    assert saved[0].num_workers == 2
    assert saved[0].language == "he"


def test_grouped_scroll_layout_has_fixed_save_footer(qapp, monkeypatch):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [])
    window = SettingsWindow(Settings())
    assert window._scroll.objectName() == "settingsScrollArea"
    assert [group.title() for group in
            window._scroll.widget().findChildren(QGroupBox)] == [
                "Input", "Transcription", "Cleanup", "History and delivery"]
    assert window._scroll.widget() is not None
    assert window._buttons is not None
    assert window._buttons.button(QDialogButtonBox.StandardButton.Save) is not None
    assert window._buttons.button(QDialogButtonBox.StandardButton.Cancel) is not None


def test_settings_roundtrip_preserves_unedited_values_and_custom_device(
        qapp, monkeypatch):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [(0, "Built-in mic")])
    original = Settings(
        hotkey="<ctrl>+<alt>", input_device="Removed USB mic",
        model_size="org/custom-whisper:v1", device="cuda",
        compute_type="float16", cpu_threads=7, num_workers=3,
        max_recording_seconds=1500, language="he", cleanup_enabled=False,
        ollama_url="https://localhost:11434/custom", ollama_model="custom:tag",
        cleanup_prompt="Custom prompt", vocabulary=["Name", "Term"],
        delivery="paste", history_enabled=False,
    )
    window = SettingsWindow(original)
    current = window._current_settings()
    assert current == original
    assert window._hotkey_summary.text() == "Ctrl + Alt"
    assert any("25 minutes" in label.text()
               for label in window._scroll.widget().findChildren(QLabel))


def test_advanced_controls_are_collapsed_then_expand(qapp, monkeypatch):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [])
    window = SettingsWindow(Settings(cpu_threads=5, num_workers=4,
                                    compute_type="bfloat16", device="cuda"))
    assert window._advanced_details.isHidden()
    window._advanced_toggle.click()
    assert not window._advanced_details.isHidden()
    assert window._cpu_threads.value() == 5
    assert window._num_workers.value() == 4
    assert window._compute.currentText() == "bfloat16"
    assert window._device_mode.currentData() == "cuda"
    window._device_mode.setCurrentIndex(window._device_mode.findData("cpu"))
    assert window._current_settings().device == "cpu"


def test_cleanup_fields_hide_and_show_with_explanation(qapp, monkeypatch):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [])
    window = SettingsWindow(Settings())
    assert not window._cleanup_details.isHidden()
    window._cleanup_enabled.setChecked(False)
    assert window._cleanup_details.isHidden()
    assert not window._cleanup_disabled_note.isHidden()
    window._cleanup_enabled.setChecked(True)
    assert not window._cleanup_details.isHidden()
    assert window._cleanup_disabled_note.isHidden()


def test_invalid_shortcut_shows_inline_error_and_keeps_dialog_open(qapp, monkeypatch):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [])
    window = SettingsWindow(Settings())
    saved = []
    window.saved.connect(saved.append)
    window.show()
    qapp.processEvents()
    window._hotkey.setText("")
    window._on_save()
    assert window.isVisible()
    assert "hotkey" in window._error.text()
    assert window._error.isVisible()
    assert window._hotkey.hasFocus()
    assert saved == []
    window.close()


def test_save_handler_failure_keeps_dialog_open_and_does_not_emit_saved(
        qapp, monkeypatch):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [])
    window = SettingsWindow(Settings())
    saved = []
    window.saved.connect(saved.append)
    window.set_save_handler(lambda _settings: (_ for _ in ()).throw(OSError("disk full")))
    window.show()
    qapp.processEvents()
    window._on_save()
    assert window.isVisible()
    assert "Save failed" in window._error.text()
    assert "disk full" in window._error.text()
    assert saved == []
    window.close()


def test_save_handler_runs_before_saved_signal_and_accept(qapp, monkeypatch):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [])
    window = SettingsWindow(Settings(max_recording_seconds=1500))
    calls = []
    window.set_save_handler(lambda settings: calls.append(settings))
    emitted = []
    window.saved.connect(lambda settings: emitted.append((settings, bool(calls))))
    window._on_save()
    assert calls and emitted == [(calls[0], True)]
    assert calls[0].max_recording_seconds == 1500
    assert not window.isVisible()


def test_runtime_status_and_reload_signal(qapp, monkeypatch):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [])
    window = SettingsWindow(Settings())
    window.set_runtime_status("Model ready: CPU / int8")
    assert window._runtime_status.text() == "Model ready: CPU / int8"
    requested = []
    window.reload_model_requested.connect(lambda: requested.append(True))
    window._reload_model.click()
    assert requested == [True]


def test_vocabulary_is_available_without_llm_cleanup(qapp, monkeypatch):
    monkeypatch.setattr(AudioCapture, "list_devices", lambda: [])
    window = SettingsWindow(Settings(cleanup_enabled=False, vocabulary=["Orpheus"]))
    window.show()
    qapp.processEvents()
    assert window._vocabulary.isVisible()
    assert window._vocabulary.isEnabled()
    assert window._vocabulary.height() <= 96
    assert window._current_settings().vocabulary == ["Orpheus"]
    window.close()
