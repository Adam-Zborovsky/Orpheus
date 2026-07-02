"""Read-only viewer over past transcriptions, with copy-to-clipboard."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                               QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from ..history import HistoryEntry

_ENTRY_ROLE = Qt.ItemDataRole.UserRole


def _preview(entry: HistoryEntry) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.ts))
    text = entry.final_text.replace("\n", " ")
    if len(text) > 70:
        text = text[:67] + "..."
    return f"{when}  —  {text}"


class HistoryWindow(QDialog):
    def __init__(self, entries: list[HistoryEntry], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Orpheus — Transcription History")
        self.setMinimumSize(520, 420)

        self._empty_label = QLabel(
            "No transcriptions yet. They'll show up here after you dictate.")
        self._empty_label.setWordWrap(True)
        self._empty_label.setVisible(not entries)

        self._list = QListWidget()
        self._list.setVisible(bool(entries))
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        # Newest first, matching HistoryStore.recent()'s own ordering contract.
        for entry in sorted(entries, key=lambda e: e.ts, reverse=True):
            item = QListWidgetItem(_preview(entry))
            item.setData(_ENTRY_ROLE, entry)
            self._list.addItem(item)

        self._preview_box = QPlainTextEdit()
        self._preview_box.setReadOnly(True)
        self._preview_box.setPlaceholderText(
            "Select an entry to see the full text.")

        self._copy_button = QPushButton("Copy to Clipboard")
        self._copy_button.setEnabled(False)
        self._copy_button.clicked.connect(self._copy_selected)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(self._copy_button)
        button_row.addStretch(1)
        button_row.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._list, 1)
        layout.addWidget(self._preview_box, 1)
        layout.addLayout(button_row)

    def _selected_entry(self) -> HistoryEntry | None:
        items = self._list.selectedItems()
        return items[0].data(_ENTRY_ROLE) if items else None

    def _on_selection_changed(self) -> None:
        entry = self._selected_entry()
        self._preview_box.setPlainText(entry.final_text if entry else "")
        self._copy_button.setEnabled(entry is not None)

    def _copy_selected(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            QApplication.clipboard().setText(entry.final_text)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(_ENTRY_ROLE)
        if entry is not None:
            QApplication.clipboard().setText(entry.final_text)
