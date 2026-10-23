"""Searchable local history viewer with explicit export and retention actions."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..history import HistoryEntry, HistoryStore

_ENTRY_ROLE = Qt.ItemDataRole.UserRole
_PAGE_SIZE = 50


def _preview(entry: HistoryEntry) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.ts))
    text = entry.final_text.replace("\n", " ")
    if len(text) > 70:
        text = text[:67] + "..."
    return f"{when}  —  {text}"


def export_to(path: Path | str, text: str) -> None:
    """Write the selected transcript as plain UTF-8 text."""
    Path(path).write_text(text, encoding="utf-8", newline="")


class HistoryWindow(QDialog):
    def __init__(self, entries: list[HistoryEntry] | None = None,
                 parent=None, *, store: HistoryStore | None = None):
        super().__init__(parent)
        self.setWindowTitle("Orpheus — Transcription History")
        self.setMinimumSize(640, 420)
        self.resize(920, 680)
        self._store = store
        self._local_entries = list(entries or [])
        self._query = ""
        self._offset = 0
        self._page_entries: list[HistoryEntry] = []
        self._review_cutoff: float | None = None
        self._review_count = 0

        self._search = QLineEdit()
        self._search.setObjectName("historySearch")
        self._search.setAccessibleName("Search transcript history")
        self._search.setPlaceholderText("Search raw and final transcripts")
        self._search.returnPressed.connect(self._run_search)
        self._search_button = QPushButton("Search")
        self._search_button.clicked.connect(self._run_search)

        self._count_label = QLabel()
        self._count_label.setObjectName("historyResultCount")
        self._count_label.setAccessibleName("History result count")
        self._count_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        self._error_label = QLabel()
        self._error_label.setObjectName("historyError")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        self._retry_button = QPushButton("Retry search")
        self._retry_button.setVisible(False)
        self._retry_button.clicked.connect(self._refresh)

        self._empty_label = QLabel("No transcriptions yet. Dictated entries will appear here.")
        self._empty_label.setWordWrap(True)
        self._empty_label.setVisible(False)
        self._list = QListWidget()
        self._list.setAccessibleName("Transcription history results")
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)

        self._preview_tabs = QTabWidget()
        self._preview_tabs.setAccessibleName("Selected transcript text")
        self._raw_box = QPlainTextEdit()
        self._raw_box.setAccessibleName("Raw transcript")
        self._raw_box.setReadOnly(True)
        self._preview_box = QPlainTextEdit()
        self._preview_box.setAccessibleName("Final transcript")
        self._preview_box.setReadOnly(True)
        self._preview_tabs.addTab(self._raw_box, "Raw transcript")
        self._preview_tabs.addTab(self._preview_box, "Final transcript")
        self._preview_tabs.setCurrentIndex(1)

        self._detail_label = QLabel("Select a transcription to review its text.")
        self._detail_label.setObjectName("historyEntryDetails")
        self._detail_label.setWordWrap(True)
        self._detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._list)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self._detail_label)
        detail_layout.addWidget(self._preview_tabs, 1)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        self._splitter = splitter

        self._previous_button = QPushButton("Previous page")
        self._previous_button.setAccessibleName("Previous history page")
        self._previous_button.clicked.connect(self._previous_page)
        self._next_button = QPushButton("Next page")
        self._next_button.setAccessibleName("Next history page")
        self._next_button.clicked.connect(self._next_page)
        self._page_label = QLabel()
        pager = QHBoxLayout()
        pager.addWidget(self._previous_button)
        pager.addWidget(self._page_label, 1, Qt.AlignmentFlag.AlignCenter)
        pager.addWidget(self._next_button)

        self._copy_button = QPushButton("Copy final transcript")
        self._copy_button.setAccessibleName("Copy selected final transcript")
        self._copy_button.setEnabled(False)
        self._copy_button.clicked.connect(self._copy_selected)
        self._export_button = QPushButton("Export selected final transcript…")
        self._export_button.setAccessibleName("Export selected final transcript")
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self._choose_export_path)
        action_row = QHBoxLayout()
        action_row.addWidget(self._copy_button)
        action_row.addWidget(self._export_button)
        action_row.addStretch(1)

        self._retention_toggle = QToolButton()
        self._retention_toggle.setText("Review older entries")
        self._retention_toggle.setAccessibleName("Show older history retention controls")
        self._retention_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._retention_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._retention_toggle.setCheckable(True)
        self._retention_toggle.toggled.connect(self._set_retention_visible)
        self._retention_panel = QWidget()
        retention_form = QFormLayout(self._retention_panel)
        self._retention_days = QSpinBox()
        self._retention_days.setRange(1, 36500)
        self._retention_days.setValue(365)
        self._retention_days.setSuffix(" days")
        self._retention_days.setAccessibleName("Older than this many days")
        self._retention_days.valueChanged.connect(self._invalidate_retention_review)
        self._retention_preview = QLabel(
            "Review the count before deleting older entries.")
        self._retention_preview.setWordWrap(True)
        self._retention_review_button = QPushButton("Review count")
        self._retention_review_button.clicked.connect(self._review_retention)
        self._retention_delete_button = QPushButton("Delete reviewed entries")
        self._retention_delete_button.setEnabled(False)
        self._retention_delete_button.clicked.connect(self._delete_reviewed)
        retention_actions = QWidget()
        retention_actions_layout = QHBoxLayout(retention_actions)
        retention_actions_layout.setContentsMargins(0, 0, 0, 0)
        retention_actions_layout.addWidget(self._retention_review_button)
        retention_actions_layout.addWidget(self._retention_delete_button)
        retention_form.addRow("Older than", self._retention_days)
        retention_form.addRow("Review", self._retention_preview)
        retention_form.addRow("", retention_actions)
        self._retention_panel.setVisible(False)
        self._retention_toggle.setEnabled(store is not None)
        self._retention_unavailable = QLabel(
            "Retention review requires an open history database.")
        self._retention_unavailable.setWordWrap(True)
        self._retention_unavailable.setVisible(store is None)

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.rejected.connect(self.reject)

        search_row = QHBoxLayout()
        search_row.addWidget(self._search, 1)
        search_row.addWidget(self._search_button)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self._count_label)
        layout.addWidget(self._error_label)
        layout.addWidget(self._retry_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._splitter, 1)
        layout.addLayout(pager)
        layout.addLayout(action_row)
        layout.addWidget(self._retention_toggle)
        layout.addWidget(self._retention_panel)
        layout.addWidget(self._retention_unavailable)
        layout.addWidget(close_buttons)

        self._refresh()

    def _filtered_local_entries(self, query: str) -> list[HistoryEntry]:
        ordered = sorted(self._local_entries, key=lambda item: (item.ts, item.id),
                         reverse=True)
        if not query:
            return ordered
        needle = query.casefold()
        return [entry for entry in ordered
                if needle in entry.raw_text.casefold()
                or needle in entry.final_text.casefold()]

    def _count(self, query: str) -> int:
        return (self._store.count(query) if self._store is not None
                else len(self._filtered_local_entries(query)))

    def _search_page(self, query: str) -> list[HistoryEntry]:
        if self._store is not None:
            return self._store.search(query, limit=_PAGE_SIZE, offset=self._offset)
        return self._filtered_local_entries(query)[self._offset:self._offset + _PAGE_SIZE]

    def _run_search(self) -> None:
        self._query = self._search.text()
        self._offset = 0
        self._refresh()

    def _refresh(self) -> None:
        self._error_label.clear()
        self._error_label.setVisible(False)
        self._retry_button.setVisible(False)
        try:
            total = self._count(self._query)
            last_offset = max(0, ((total - 1) // _PAGE_SIZE) * _PAGE_SIZE)
            self._offset = min(self._offset, last_offset)
            page = self._search_page(self._query)
        except Exception as exc:
            self._page_entries = []
            self._list.clear()
            self._list.setVisible(False)
            self._splitter.setVisible(False)
            self._empty_label.setVisible(False)
            self._count_label.setText("History could not be loaded.")
            self._error_label.setText(
                f"Search failed ({type(exc).__name__}). Retry the search or close History.")
            self._error_label.setVisible(True)
            self._retry_button.setVisible(True)
            self._set_pager(0)
            self._set_entry(None)
            return

        self._page_entries = page
        self._list.clear()
        for entry in page:
            item = QListWidgetItem(_preview(entry))
            item.setData(_ENTRY_ROLE, entry)
            self._list.addItem(item)
        has_entries = bool(page)
        self._list.setVisible(has_entries)
        self._splitter.setVisible(has_entries)
        self._empty_label.setVisible(not has_entries)
        if total == 0:
            self._empty_label.setText(
                "No matches. Edit the search to find another transcription."
                if self._query else
                "No transcriptions yet. Dictated entries will appear here.")
            self._count_label.setText("0 matching transcriptions")
        else:
            first = self._offset + 1
            last = self._offset + len(page)
            self._count_label.setText(
                f"Showing {first}–{last} of {total} matching transcriptions")
        self._set_pager(total)
        if not page:
            self._set_entry(None)

    def _set_pager(self, total: int) -> None:
        self._previous_button.setEnabled(self._offset > 0)
        self._next_button.setEnabled(self._offset + _PAGE_SIZE < total)
        page = self._offset // _PAGE_SIZE + 1
        pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self._page_label.setText(f"Page {page} of {pages}")

    def _next_page(self) -> None:
        self._offset += _PAGE_SIZE
        self._refresh()

    def _previous_page(self) -> None:
        self._offset = max(0, self._offset - _PAGE_SIZE)
        self._refresh()

    def _selected_entry(self) -> HistoryEntry | None:
        items = self._list.selectedItems()
        return items[0].data(_ENTRY_ROLE) if items else None

    def _set_entry(self, entry: HistoryEntry | None) -> None:
        self._raw_box.setPlainText(entry.raw_text if entry else "")
        self._preview_box.setPlainText(entry.final_text if entry else "")
        if entry is None:
            self._detail_label.setText("Select a transcription to review its text.")
        else:
            self._detail_label.setText(
                f"{entry.word_count} words · Processing time: "
                f"{entry.duration_s:.2f} seconds")
        enabled = entry is not None
        self._copy_button.setEnabled(enabled)
        self._export_button.setEnabled(enabled)

    def _on_selection_changed(self) -> None:
        self._set_entry(self._selected_entry())

    def _copy_selected(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            QApplication.clipboard().setText(entry.final_text)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(_ENTRY_ROLE)
        if entry is not None:
            QApplication.clipboard().setText(entry.final_text)

    def _choose_export_path(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export selected final transcript", "transcript.txt",
            "Text files (*.txt);;All files (*)",
        )
        if path:
            try:
                export_to(path, entry.final_text)
            except OSError as exc:
                self._error_label.setText(
                    f"Export failed ({type(exc).__name__}). Choose another file and retry.")
                self._error_label.setVisible(True)

    def _set_retention_visible(self, visible: bool) -> None:
        self._retention_panel.setVisible(visible and self._store is not None)
        self._retention_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)
        self._retention_toggle.setText(
            "Hide older-entry retention" if visible else "Review older entries")
        self._retention_toggle.setAccessibleName(
            "Hide older history retention controls" if visible
            else "Show older history retention controls")

    def _retention_cutoff(self) -> float:
        return time.time() - self._retention_days.value() * 24 * 60 * 60

    def _invalidate_retention_review(self, _days: int | None = None) -> None:
        self._review_cutoff = None
        self._review_count = 0
        self._retention_delete_button.setEnabled(False)
        self._retention_preview.setText(
            "Review the count before deleting older entries.")

    def _review_retention(self) -> None:
        if self._store is None:
            return
        self._invalidate_retention_review()
        try:
            cutoff = self._retention_cutoff()
            count = self._store.count_before(cutoff)
        except Exception as exc:
            self._error_label.setText(
                f"Retention review failed ({type(exc).__name__}). Retry the review.")
            self._error_label.setVisible(True)
            return
        self._review_cutoff = cutoff
        self._review_count = count
        self._retention_preview.setText(
            f"{count} entries are older than {self._retention_days.value()} days.")
        self._retention_delete_button.setEnabled(count > 0)

    def _delete_reviewed(self) -> None:
        if self._store is None or self._review_cutoff is None or self._review_count < 1:
            return
        confirmation = QMessageBox(
            QMessageBox.Icon.Warning, "Delete older history?",
            f"Delete these {self._review_count} history entries older than "
            f"{self._retention_days.value()} days? This cannot be undone.",
            parent=self,
        )
        delete = confirmation.addButton("Delete reviewed entries", QMessageBox.ButtonRole.DestructiveRole)
        cancel = confirmation.addButton(QMessageBox.StandardButton.Cancel)
        confirmation.setDefaultButton(cancel)
        confirmation.exec()
        if confirmation.clickedButton() is not delete:
            return
        try:
            deleted = self._store.delete_before(
                self._review_cutoff, expected_count=self._review_count,
            )
        except Exception as exc:
            self._error_label.setText(
                f"Deletion stopped ({type(exc).__name__}). Review the count again.")
            self._error_label.setVisible(True)
            self._review_cutoff = None
            self._review_count = 0
            self._retention_delete_button.setEnabled(False)
            return
        self._retention_preview.setText(f"Deleted {deleted} reviewed entries.")
        self._review_cutoff = None
        self._review_count = 0
        self._retention_delete_button.setEnabled(False)
        self._refresh()
