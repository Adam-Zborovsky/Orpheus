import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt

from orpheus.history import HistoryEntry
from orpheus.history import HistoryStore
from orpheus.ui.history_window import HistoryWindow, export_to

ENTRY_A = HistoryEntry(id=1, ts=1_700_000_000.0, raw_text="hello world",
                       final_text="Hello, world.", duration_s=1.2, word_count=2)
ENTRY_B = HistoryEntry(id=2, ts=1_700_000_100.0, raw_text="second one",
                       final_text="Second one.", duration_s=0.8, word_count=2)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_empty_state_shows_placeholder(qapp):
    window = HistoryWindow([])
    assert not window._empty_label.isHidden()
    assert window._list.isHidden()
    assert window._list.count() == 0


def test_populates_list_newest_first(qapp):
    window = HistoryWindow([ENTRY_A, ENTRY_B])
    assert window._empty_label.isHidden()
    assert window._list.count() == 2
    # newest (higher ts) shown first
    assert "Second one" in window._list.item(0).text()
    assert "Hello, world" in window._list.item(1).text()


def test_selecting_entry_shows_full_text(qapp):
    window = HistoryWindow([ENTRY_A])
    window._list.setCurrentRow(0)
    assert window._preview_box.toPlainText() == ENTRY_A.final_text
    assert window._copy_button.isEnabled()


def test_no_selection_disables_copy(qapp):
    window = HistoryWindow([ENTRY_A])
    assert not window._copy_button.isEnabled()
    assert window._preview_box.toPlainText() == ""


def test_copy_button_copies_selected_text(qapp):
    from PySide6.QtWidgets import QApplication

    window = HistoryWindow([ENTRY_A, ENTRY_B])
    window._list.setCurrentRow(0)  # newest first -> ENTRY_B
    window._copy_button.click()
    assert QApplication.clipboard().text() == ENTRY_B.final_text


def test_double_click_copies_text(qapp):
    from PySide6.QtWidgets import QApplication

    window = HistoryWindow([ENTRY_A])
    item = window._list.item(0)
    window._on_double_clicked(item)
    assert QApplication.clipboard().text() == ENTRY_A.final_text


def test_master_detail_shows_raw_final_word_count_and_processing_time(qapp):
    window = HistoryWindow([ENTRY_A])
    window._list.setCurrentRow(0)
    assert window._raw_box.toPlainText() == ENTRY_A.raw_text
    assert window._preview_box.toPlainText() == ENTRY_A.final_text
    assert "2 words" in window._detail_label.text()
    assert "Processing time: 1.20 seconds" in window._detail_label.text()
    assert window._preview_tabs.tabText(0) == "Raw transcript"
    assert window._preview_tabs.tabText(1) == "Final transcript"


def test_store_search_pages_more_than_two_hundred_entries(qapp, tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    for i in range(205):
        store.add(f"raw {i}", f"entry {i}", ts=50)
    window = HistoryWindow(store=store)
    assert window._list.count() == 50
    assert "of 205" in window._count_label.text()
    assert not window._previous_button.isEnabled()
    assert window._next_button.isEnabled()
    window._next_button.click()
    assert window._list.count() == 50
    assert "Page 2 of 5" == window._page_label.text()
    assert window._list.item(0).data(Qt.ItemDataRole.UserRole).id == 155
    window._previous_button.click()
    assert "Page 1 of 5" == window._page_label.text()
    store.close()


def test_store_search_is_literal_and_matches_raw_or_final(qapp, tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.add("raw value%_one", "cleanup", ts=1)
    store.add("plain", "final value%_two", ts=2)
    store.add("valueXXone", "other", ts=3)
    window = HistoryWindow(store=store)
    window._search.setText("value%_")
    window._run_search()
    assert window._list.count() == 2
    assert "of 2" in window._count_label.text()
    store.close()


def test_no_results_has_recovery_prompt(qapp, tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    window = HistoryWindow(store=store)
    window._search.setText("not present")
    window._run_search()
    assert window._list.isHidden()
    assert "No matches" in window._empty_label.text()
    assert "Edit the search" in window._empty_label.text()
    store.close()


def test_database_error_shows_retry_and_recovers(qapp, tmp_path):
    path = tmp_path / "history.sqlite3"
    store = HistoryStore(path)
    window = HistoryWindow(store=store)
    store.close()
    window._refresh()
    assert not window._error_label.isHidden()
    assert not window._retry_button.isHidden()
    window._store = HistoryStore(path)
    window._retry_button.click()
    assert window._error_label.isHidden()
    assert "No transcriptions yet" in window._empty_label.text()
    window._store.close()


def test_export_helper_writes_selected_unicode_as_utf8(tmp_path):
    target = tmp_path / "selected.txt"
    text = "שלום — café 😀"
    export_to(target, text)
    assert target.read_bytes() == text.encode("utf-8")


def test_export_action_is_labeled_as_selected_final_text(qapp):
    window = HistoryWindow([ENTRY_A])
    assert window._export_button.text() == "Export selected final transcript…"
    assert window._export_button.accessibleName() == "Export selected final transcript"


def test_retention_requires_review_and_deletes_only_confirmed_count(
        qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    store = HistoryStore(tmp_path / "history.sqlite3")
    store.add("old one", "old one", ts=10)
    store.add("old two", "old two", ts=20)
    store.add("recent", "recent", ts=30)
    window = HistoryWindow(store=store)
    window._retention_days.setValue(2)
    monkeypatch.setattr(window, "_retention_cutoff", lambda: 25)
    window._retention_toggle.click()
    assert not window._retention_panel.isHidden()
    assert not window._retention_delete_button.isEnabled()
    window._retention_review_button.click()
    assert "2 entries" in window._retention_preview.text()
    assert window._retention_delete_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: next(
        button for button in self.buttons() if self.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole))
    window._retention_delete_button.click()
    assert store.count_before(25) == 0
    assert store.count() == 1
    assert "Deleted 2" in window._retention_preview.text()
    store.close()


def test_retention_stops_when_count_changes_after_review(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    store = HistoryStore(tmp_path / "history.sqlite3")
    store.add("old", "old", ts=10)
    window = HistoryWindow(store=store)
    monkeypatch.setattr(window, "_retention_cutoff", lambda: 25)
    window._review_retention()
    store.add("new older item", "new older item", ts=15)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: next(
        button for button in self.buttons() if self.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole))
    window._delete_reviewed()
    assert store.count_before(25) == 2
    assert "Deletion stopped" in window._error_label.text()
    store.close()


def test_retention_review_is_invalidated_when_days_change(qapp, tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.add("old", "old", ts=10)
    window = HistoryWindow(store=store)
    monkeypatch.setattr(window, "_retention_cutoff", lambda: 25)
    window._review_retention()
    assert window._retention_delete_button.isEnabled()
    window._retention_days.setValue(window._retention_days.value() + 1)
    assert not window._retention_delete_button.isEnabled()
    assert window._review_cutoff is None
    assert "Review the count" in window._retention_preview.text()
    store.close()


def test_accessible_names_for_search_list_paging_and_retention(qapp, tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    window = HistoryWindow(store=store)
    assert window._search.accessibleName() == "Search transcript history"
    assert window._list.accessibleName() == "Transcription history results"
    assert window._next_button.accessibleName() == "Next history page"
    assert window._previous_button.accessibleName() == "Previous history page"
    assert window._retention_days.accessibleName() == "Older than this many days"
    store.close()


def test_page_is_clamped_after_reviewed_entries_are_removed(qapp, tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    for i in range(55):
        store.add(str(i), str(i), ts=i)
    window = HistoryWindow(store=store)
    window._next_page()
    assert window._offset == 50
    store.delete_before(50, expected_count=50)
    window._refresh()
    assert window._offset == 0
    assert window._list.count() == 5
    assert window._page_label.text() == "Page 1 of 1"
    store.close()
