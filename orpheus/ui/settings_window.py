"""Grouped, scrollable native Qt settings dialog."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from ..audio import AudioCapture
from ..settings import DEFAULT_CLEANUP_PROMPT, Settings, validate_settings

_MODEL_SIZES = ["large-v3-turbo", "large-v3", "distil-large-v3", "medium",
                "small", "base", "tiny"]
_SYSTEM_DEFAULT = "System default"
_COMPUTE_TYPES = [
    "auto", "default", "float32", "float16", "bfloat16", "int8",
    "int8_float32", "int8_float16", "int8_bfloat16", "int16",
]


class SettingsWindow(QDialog):
    saved = Signal(object)  # Settings
    history_requested = Signal()
    reload_model_requested = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Orpheus Settings")
        self.setMinimumSize(500, 500)
        self.resize(620, 720)
        self._original_settings = replace(settings, vocabulary=list(settings.vocabulary))
        self._save_handler = None

        self._hotkey = QLineEdit(settings.hotkey)
        self._hotkey.setToolTip(
            "Use pynput syntax. Modifier-only bindings such as <ctrl>+<alt> are valid.")
        self._hotkey_summary = QLabel()
        self._hotkey_summary.setObjectName("hotkeySummary")
        self._hotkey_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._hotkey.textChanged.connect(self._update_hotkey_summary)
        self._update_hotkey_summary(settings.hotkey)

        self._device = QComboBox()
        self._device.addItem(_SYSTEM_DEFAULT, "")
        try:
            for _, name in AudioCapture.list_devices():
                self._device.addItem(name, name)
        except Exception:
            pass  # Audio enumeration is optional; retain the configured device.
        self._select_input_device(settings.input_device)

        self._model = QComboBox()
        self._model.setEditable(True)
        self._model.addItems(_MODEL_SIZES)
        self._model.setCurrentText(settings.model_size)

        self._language = QComboBox()
        for label, value in [("Auto-detect", "auto"), ("English", "en"),
                             ("Hebrew", "he")]:
            self._language.addItem(label, value)
        self._language.setCurrentIndex(self._language.findData(settings.language))

        self._runtime_status = QLabel("Runtime status: waiting for model load")
        self._runtime_status.setObjectName("runtimeStatus")
        self._runtime_status.setWordWrap(True)
        self._runtime_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._reload_model = QPushButton("Reload model")
        self._reload_model.clicked.connect(self.reload_model_requested.emit)

        self._processing_device = settings.device  # legacy field retained for callers
        self._compute = QComboBox()
        self._compute.addItems(_COMPUTE_TYPES)
        self._compute.setCurrentText(settings.compute_type)
        self._compute.setToolTip("Compute precision used by the selected backend.")

        self._cpu_threads = QSpinBox()
        self._cpu_threads.setRange(0, 128)
        self._cpu_threads.setValue(settings.cpu_threads)
        self._cpu_threads.setSpecialValueText("Auto (all cores)")
        self._cpu_threads.setToolTip("0 uses all logical cores.")

        self._num_workers = QSpinBox()
        self._num_workers.setRange(1, 16)
        self._num_workers.setValue(settings.num_workers)

        self._device_mode = QComboBox()
        for label, value in [("Automatic", "auto"), ("CPU", "cpu"),
                             ("CUDA", "cuda")]:
            self._device_mode.addItem(label, value)
        self._device_mode.setCurrentIndex(self._device_mode.findData(settings.device))
        self._device_mode.currentIndexChanged.connect(
            lambda _index: setattr(self, "_processing_device",
                                   self._device_mode.currentData()))

        self._advanced_toggle = QToolButton()
        self._advanced_toggle.setText("Show advanced transcription")
        self._advanced_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.setChecked(False)

        self._cleanup_enabled = QCheckBox("Enable LLM cleanup")
        self._cleanup_enabled.setChecked(settings.cleanup_enabled)
        self._ollama_url = QLineEdit(settings.ollama_url)
        self._ollama_model = QLineEdit(settings.ollama_model)
        self._prompt = QPlainTextEdit(settings.cleanup_prompt)
        self._prompt.setMinimumHeight(112)
        self._vocabulary = QPlainTextEdit("\n".join(settings.vocabulary))
        self._vocabulary.setPlaceholderText("One term per line")
        self._vocabulary.setMinimumHeight(84)
        self._vocabulary.setMaximumHeight(96)
        self._vocabulary.setToolTip("Keep one spelling per line.")
        self._reset_prompt = QPushButton("Reset prompt to default")
        self._reset_prompt.clicked.connect(
            lambda: self._prompt.setPlainText(DEFAULT_CLEANUP_PROMPT))

        self._delivery = QComboBox()
        for label, value in [("Type text", "type"), ("Paste text", "paste")]:
            self._delivery.addItem(label, value)
        self._delivery.setCurrentIndex(self._delivery.findData(settings.delivery))
        self._history_enabled = QCheckBox("Save transcript history")
        self._history_enabled.setChecked(settings.history_enabled)
        self._show_history = QPushButton("Show transcription history")
        self._show_history.clicked.connect(self.history_requested.emit)

        self._error = QLabel()
        self._error.setObjectName("settingsError")
        self._error.setWordWrap(True)
        error_palette = self._error.palette()
        error_palette.setColor(
            QPalette.ColorRole.WindowText,
            error_palette.color(QPalette.ColorRole.BrightText),
        )
        self._error.setPalette(error_palette)
        self._error.setVisible(False)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 12, 16, 16)
        content_layout.setSpacing(14)
        content_layout.addWidget(self._input_group())
        content_layout.addWidget(self._transcription_group(settings))
        content_layout.addWidget(self._cleanup_group())
        content_layout.addWidget(self._history_delivery_group())
        content_layout.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("settingsScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(content)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setDefault(True)
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._scroll, 1)
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(16, 8, 16, 12)
        footer_layout.setSpacing(6)
        footer_layout.addWidget(self._error)
        footer_layout.addWidget(self._buttons)
        layout.addWidget(footer)

        self._advanced_toggle.toggled.connect(self._set_advanced_visible)
        self._cleanup_enabled.toggled.connect(self._set_cleanup_enabled)
        self._set_cleanup_enabled(settings.cleanup_enabled)

    def _input_group(self) -> QGroupBox:
        group = QGroupBox("Input")
        form = QFormLayout(group)
        form.addRow("Shortcut", self._hotkey_summary)
        self._hotkey_toggle = QToolButton()
        self._hotkey_toggle.setText("Edit shortcut…")
        self._hotkey_toggle.setCheckable(True)
        editor = QWidget()
        editor_form = QFormLayout(editor)
        editor_form.setContentsMargins(0, 0, 0, 0)
        editor_form.addRow("Shortcut syntax", self._hotkey)
        editor.hide()
        self._hotkey_toggle.toggled.connect(editor.setVisible)
        form.addRow(self._hotkey_toggle)
        form.addRow(editor)
        form.addRow("Microphone", self._device)
        return group

    def _transcription_group(self, settings: Settings) -> QGroupBox:
        group = QGroupBox("Transcription")
        form = QFormLayout(group)
        form.addRow("Whisper model", self._model)
        form.addRow("Language", self._language)
        form.addRow("Vocabulary", self._vocabulary)
        limit = QLabel(
            f"Up to {settings.max_recording_seconds // 60} minutes; "
            "recording stops and transcribes at the limit.")
        limit.setWordWrap(True)
        form.addRow("Recording limit", limit)
        form.addRow("Model status", self._runtime_status)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.addWidget(self._reload_model, 0, Qt.AlignmentFlag.AlignLeft)
        actions_layout.addStretch(1)
        form.addRow("", actions)

        advanced = QWidget()
        self._advanced_details = advanced
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(0, 6, 0, 0)
        advanced_form.addRow("Processing device", self._device_mode)
        advanced_form.addRow("Compute type", self._compute)
        advanced_form.addRow("CPU threads", self._cpu_threads)
        advanced_form.addRow("Workers", self._num_workers)
        advanced.setVisible(False)
        form.addRow(self._advanced_toggle)
        form.addRow(advanced)
        return group

    def _cleanup_group(self) -> QGroupBox:
        group = QGroupBox("Cleanup")
        layout = QVBoxLayout(group)
        layout.addWidget(self._cleanup_enabled)
        self._cleanup_details = QWidget()
        form = QFormLayout(self._cleanup_details)
        form.addRow("Ollama URL", self._ollama_url)
        form.addRow("Ollama model", self._ollama_model)
        form.addRow("Cleanup prompt", self._prompt)
        form.addRow("", self._reset_prompt)
        self._cleanup_disabled_note = QLabel(
            "Cleanup is off. Raw transcription text will be delivered directly.")
        self._cleanup_disabled_note.setWordWrap(True)
        layout.addWidget(self._cleanup_details)
        layout.addWidget(self._cleanup_disabled_note)
        return group

    def _history_delivery_group(self) -> QGroupBox:
        group = QGroupBox("History and delivery")
        form = QFormLayout(group)
        form.addRow("Text delivery", self._delivery)
        form.addRow("", self._history_enabled)
        form.addRow("", self._show_history)
        return group

    @staticmethod
    def _readable_hotkey(value: str) -> str:
        names = {
            "<ctrl>": "Ctrl", "<alt>": "Alt", "<shift>": "Shift",
            "<cmd>": "Windows", "<space>": "Space", "<enter>": "Enter",
            "<tab>": "Tab", "<esc>": "Esc",
        }
        parts = value.split("+")
        return " + ".join(names.get(part.lower(), part.strip("<>").capitalize())
                           for part in parts if part)

    def _update_hotkey_summary(self, value: str) -> None:
        self._hotkey_summary.setText(self._readable_hotkey(value) or "No shortcut")

    def _select_input_device(self, configured_name: str) -> None:
        index = self._device.findData(configured_name)
        if index < 0 and configured_name:
            self._device.addItem(configured_name, configured_name)
            index = self._device.count() - 1
        self._device.setCurrentIndex(max(0, index))

    def _set_advanced_visible(self, visible: bool) -> None:
        self._advanced_details.setVisible(visible)
        self._advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)
        self._advanced_toggle.setText(
            "Hide advanced transcription" if visible
            else "Show advanced transcription")

    def _set_cleanup_enabled(self, enabled: bool) -> None:
        self._cleanup_details.setVisible(enabled)
        self._cleanup_disabled_note.setVisible(not enabled)

    def set_save_handler(self, handler) -> None:
        """Set the durable save/apply callback run before saved is emitted."""
        self._save_handler = handler

    def set_runtime_status(self, text: str) -> None:
        self._runtime_status.setText(text)

    def _current_settings(self) -> Settings:
        return replace(
            self._original_settings,
            hotkey=self._hotkey.text().strip(),
            input_device=self._device.currentData() or "",
            model_size=self._model.currentText().strip(),
            device=self._device_mode.currentData(),
            compute_type=self._compute.currentText(),
            cpu_threads=self._cpu_threads.value(),
            num_workers=self._num_workers.value(),
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

    def _show_error(self, message: str, settings: Settings | None = None) -> None:
        self._error.setText(message)
        self._error.setVisible(True)
        if settings is None:
            return
        field_name = message.split(":", 1)[0]
        field_by_name = {
            "hotkey": self._hotkey,
            "input_device": self._device,
            "model_size": self._model,
            "device": self._device_mode,
            "compute_type": self._compute,
            "cpu_threads": self._cpu_threads,
            "num_workers": self._num_workers,
            "max_recording_seconds": self._model,
            "language": self._language,
            "cleanup_enabled": self._cleanup_enabled,
            "ollama_url": self._ollama_url,
            "ollama_model": self._ollama_model,
            "cleanup_prompt": self._prompt,
            "vocabulary": self._vocabulary,
            "delivery": self._delivery,
            "history_enabled": self._history_enabled,
        }
        field = field_by_name.get(field_name)
        if field in (self._device_mode, self._compute,
                     self._cpu_threads, self._num_workers):
            self._advanced_toggle.setChecked(True)
        if field in (self._ollama_url, self._ollama_model, self._prompt):
            self._cleanup_enabled.setChecked(True)
        if field is self._hotkey:
            self._hotkey_toggle.setChecked(True)
        if field is not None:
            field.setFocus(Qt.FocusReason.OtherFocusReason)
            self._scroll.ensureWidgetVisible(field)

    def _on_save(self) -> None:
        settings = self._current_settings()
        self._error.clear()
        self._error.setVisible(False)
        try:
            validate_settings(settings)
        except Exception as exc:
            self._show_error(str(exc), settings)
            return
        try:
            if self._save_handler is not None:
                self._save_handler(settings)
        except Exception as exc:
            self._show_error(f"Save failed: {exc}")
            return
        self._original_settings = replace(settings, vocabulary=list(settings.vocabulary))
        self.saved.emit(settings)
        self.accept()
