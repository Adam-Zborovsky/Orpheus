"""Qt settings dialog: edits a Settings object and emits saved(Settings)."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QSpinBox, QVBoxLayout)

from ..audio import AudioCapture
from ..hotkey import validate_hotkey
from ..settings import DEFAULT_CLEANUP_PROMPT, Settings

_MODEL_SIZES = ["large-v3-turbo", "large-v3", "distil-large-v3", "medium",
                "small", "base", "tiny"]
_SYSTEM_DEFAULT = "System default"


class SettingsWindow(QDialog):
    saved = Signal(object)  # Settings
    history_requested = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Orpheus Settings")
        self.setMinimumWidth(480)
        self._num_workers = settings.num_workers  # not exposed in UI; preserved

        self._hotkey = QLineEdit(settings.hotkey)
        self._hotkey.setToolTip(
            "pynput syntax, e.g. <ctrl>+<alt>+<space> or <f9>")

        self._device = QComboBox()
        self._device.addItem(_SYSTEM_DEFAULT)
        try:
            for _, name in AudioCapture.list_devices():
                self._device.addItem(name)
        except Exception:
            pass  # no PortAudio? leave only the default entry
        if settings.input_device:
            index = self._device.findText(settings.input_device)
            if index < 0:
                self._device.addItem(settings.input_device)
                index = self._device.count() - 1
            self._device.setCurrentIndex(index)

        self._model = QComboBox()
        self._model.setEditable(True)
        self._model.addItems(_MODEL_SIZES)
        self._model.setCurrentText(settings.model_size)

        self._compute = QComboBox()
        self._compute.setEditable(True)
        self._compute.addItems(["int8", "int8_float32", "float32", "float16"])
        self._compute.setCurrentText(settings.compute_type)
        self._compute.setToolTip(
            "CPU: int8 (fastest) or float32 (most accurate). "
            "float16 is GPU-only and falls back to int8 on CPU.")

        self._cpu_threads = QSpinBox()
        self._cpu_threads.setRange(0, 128)
        self._cpu_threads.setValue(settings.cpu_threads)
        self._cpu_threads.setSpecialValueText("Auto (all cores)")  # shown at 0
        self._cpu_threads.setToolTip(
            "CPU threads for transcription. 0 = use all logical cores.")

        self._language = QComboBox()
        for label, value in [("Auto-detect", "auto"), ("English", "en"),
                             ("Hebrew", "he")]:
            self._language.addItem(label, value)
        self._language.setCurrentIndex(
            self._language.findData(settings.language))

        self._cleanup_enabled = QCheckBox("Enable LLM cleanup")
        self._cleanup_enabled.setChecked(settings.cleanup_enabled)
        self._ollama_url = QLineEdit(settings.ollama_url)
        self._ollama_model = QLineEdit(settings.ollama_model)

        self._prompt = QPlainTextEdit(settings.cleanup_prompt)
        self._prompt.setMinimumHeight(120)
        reset_prompt = QPushButton("Reset prompt to default")
        reset_prompt.clicked.connect(
            lambda: self._prompt.setPlainText(DEFAULT_CLEANUP_PROMPT))

        self._vocabulary = QPlainTextEdit("\n".join(settings.vocabulary))
        self._vocabulary.setPlaceholderText("One term per line")
        self._vocabulary.setMinimumHeight(80)

        self._delivery = QComboBox()
        for label, value in [("Type out (SendInput)", "type"),
                             ("Clipboard paste", "paste")]:
            self._delivery.addItem(label, value)
        self._delivery.setCurrentIndex(self._delivery.findData(settings.delivery))

        self._history_enabled = QCheckBox("Save transcript history")
        self._history_enabled.setChecked(settings.history_enabled)
        show_history = QPushButton("Show Transcriptions…")
        show_history.clicked.connect(self.history_requested.emit)

        form = QFormLayout()
        form.addRow("Hotkey", self._hotkey)
        form.addRow("Microphone", self._device)
        form.addRow("Whisper model", self._model)
        form.addRow("Compute type", self._compute)
        form.addRow("CPU threads", self._cpu_threads)
        form.addRow("Language", self._language)
        form.addRow("", self._cleanup_enabled)
        form.addRow("Ollama URL", self._ollama_url)
        form.addRow("Ollama model", self._ollama_model)
        form.addRow("Cleanup prompt", self._prompt)
        form.addRow("", reset_prompt)
        form.addRow("Vocabulary", self._vocabulary)
        form.addRow("Text delivery", self._delivery)
        form.addRow("", self._history_enabled)
        form.addRow("", show_history)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _current_settings(self) -> Settings:
        device = self._device.currentText()
        return Settings(
            hotkey=self._hotkey.text().strip(),
            input_device="" if device == _SYSTEM_DEFAULT else device,
            model_size=self._model.currentText().strip(),
            device="auto",  # CUDA/CPU choice is automatic per spec
            compute_type=self._compute.currentText(),
            cpu_threads=self._cpu_threads.value(),
            num_workers=self._num_workers,
            language=self._language.currentData(),
            cleanup_enabled=self._cleanup_enabled.isChecked(),
            ollama_url=self._ollama_url.text().strip(),
            ollama_model=self._ollama_model.text().strip(),
            cleanup_prompt=self._prompt.toPlainText(),
            vocabulary=[line.strip() for line in
                        self._vocabulary.toPlainText().splitlines()
                        if line.strip()],
            delivery=self._delivery.currentData(),
            history_enabled=self._history_enabled.isChecked(),
        )

    def _on_save(self) -> None:
        settings = self._current_settings()
        if not validate_hotkey(settings.hotkey):
            QMessageBox.warning(
                self, "Invalid hotkey",
                f"'{settings.hotkey}' is not a valid hotkey.\n"
                "Use pynput syntax, e.g. <ctrl>+<alt>+<space> or <f9>.")
            return
        self.saved.emit(settings)
        self.accept()
