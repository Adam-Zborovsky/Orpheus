"""Persistent, explicit recovery actions for text that could not be delivered."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QVBoxLayout)


class RecoveryWindow(QDialog):
    settings_requested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle("Orpheus — Recover text")
        self.resize(680, 520)
        self.setMinimumSize(420, 350)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._list = QListWidget()
        self._list.setAccessibleName("Transcripts needing attention")
        self._list.setMaximumHeight(150)
        self._list.itemSelectionChanged.connect(self._select)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setAccessibleName("Complete recovered transcript")
        self._text.setPlaceholderText("Select a transcript to copy or retry.")
        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._confirm = QCheckBox("I understand that retrying may duplicate text already inserted")
        self._confirm.toggled.connect(self._update_actions)
        self._discard_confirm = QCheckBox("I have saved this text and can dismiss the unsaved copy")
        self._discard_confirm.toggled.connect(self._update_actions)
        self._copy = QPushButton("Copy text")
        self._copy.clicked.connect(self._copy_text)
        self._retry = QPushButton("Retry insertion…")
        self._retry.clicked.connect(self._retry_text)
        self._dismiss = QPushButton("Dismiss copy")
        self._dismiss.clicked.connect(self._dismiss_text)
        self._cancel_retry = QPushButton("Cancel retry")
        self._cancel_retry.clicked.connect(controller.cancel_retry)
        settings = QPushButton("Open Settings")
        settings.clicked.connect(self.settings_requested.emit)
        reload_model = QPushButton("Reload model")
        reload_model.clicked.connect(controller.preload)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        actions = QHBoxLayout()
        for button in (self._copy, self._retry, self._dismiss):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self._cancel_retry)
        footer = QHBoxLayout()
        footer.addWidget(settings)
        footer.addWidget(reload_model)
        footer.addStretch()
        footer.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self._status)
        layout.addWidget(self._list)
        layout.addWidget(self._text, 1)
        layout.addWidget(self._detail)
        layout.addWidget(self._confirm)
        layout.addWidget(self._discard_confirm)
        layout.addLayout(actions)
        layout.addLayout(footer)
        controller.recovery_changed.connect(self.refresh)
        controller.status_changed.connect(self._show_status)
        self.refresh()

    def _selected(self):
        current = self._list.currentItem()
        return current.data(Qt.ItemDataRole.UserRole) if current else None

    def _show_status(self, text):
        self._status.setText(text)

    def refresh(self):
        selected = self._selected()
        selected_id = selected.id if selected else None
        self._list.blockSignals(True)
        self._list.clear()
        for entry in self._controller.recoveries:
            when = time.strftime("%H:%M", time.localtime(entry.created_at))
            persistence = "In history" if entry.history_id is not None else "Unsaved"
            item = QListWidgetItem(f"{when} · {persistence} · {entry.final_text[:65].replace(chr(10), ' ')}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._list.addItem(item)
            if entry.id == selected_id:
                self._list.setCurrentItem(item)
        if self._list.currentRow() < 0 and self._list.count():
            self._list.setCurrentRow(0)
        self._list.blockSignals(False)
        message = self._controller.last_error or "No errors need attention."
        self._status.setText(message)
        self._cancel_retry.setEnabled(self._controller._armed_retry is not None)
        self._select()

    def _select(self):
        item = self._selected()
        self._text.setPlainText(item.final_text if item else "")
        self._confirm.setChecked(False)
        self._discard_confirm.setChecked(False)
        self._confirm.setVisible(bool(item and item.partial))
        self._discard_confirm.setVisible(bool(item and item.history_id is None))
        if item:
            lifetime = ("The saved transcript is also in History." if item.history_id is not None else
                        "This copy is only in memory and will be lost when Orpheus exits. Copy or export it first.")
            self._detail.setText(f"{item.error}\n{lifetime}")
        else:
            self._detail.setText("Errors remain available here after notifications disappear. Open Settings to correct a microphone or model problem.")
        self._update_actions()

    def _update_actions(self):
        item = self._selected()
        self._copy.setEnabled(item is not None)
        self._retry.setEnabled(bool(item and (not item.partial or self._confirm.isChecked())))
        self._dismiss.setEnabled(bool(item and (item.history_id is not None or self._discard_confirm.isChecked())))

    def _copy_text(self):
        item = self._selected()
        if item:
            QApplication.clipboard().setText(item.final_text)
            self._status.setText("Text copied. Paste it into your destination.")

    def _retry_text(self):
        item = self._selected()
        if item and self._retry.isEnabled():
            try:
                self._controller.arm_retry(item.id)
            except (RuntimeError, ValueError) as exc:
                self._status.setText(str(exc))
            else:
                self._status.setText(self._controller.status)
                self._cancel_retry.setEnabled(True)

    def _dismiss_text(self):
        item = self._selected()
        if item and self._dismiss.isEnabled():
            self._controller.dismiss_recovery(item.id)
