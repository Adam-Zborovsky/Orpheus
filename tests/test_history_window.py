import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from orpheus.history import HistoryEntry
from orpheus.ui.history_window import HistoryWindow

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
