from orpheus.history import HistoryStore
import sqlite3
import pytest


def make_store(tmp_path):
    return HistoryStore(tmp_path / "history.sqlite3")


def test_add_and_recent(tmp_path):
    store = make_store(tmp_path)
    entry_id = store.add("hello world um", "Hello, world.", duration_s=1.5)
    assert entry_id == 1
    entries = store.recent()
    assert len(entries) == 1
    assert entries[0].raw_text == "hello world um"
    assert entries[0].final_text == "Hello, world."
    assert entries[0].duration_s == 1.5
    assert entries[0].word_count == 2
    store.close()


def test_recent_newest_first_and_limit(tmp_path):
    store = make_store(tmp_path)
    store.add("a", "first", ts=100.0)
    store.add("b", "second", ts=200.0)
    store.add("c", "third", ts=300.0)
    entries = store.recent(limit=2)
    assert [e.final_text for e in entries] == ["third", "second"]
    store.close()


def test_stats(tmp_path):
    store = make_store(tmp_path)
    store.add("x", "one two three")
    store.add("y", "four five")
    assert store.stats() == {"entries": 2, "words": 5}
    store.close()


def test_stats_empty(tmp_path):
    store = make_store(tmp_path)
    assert store.stats() == {"entries": 0, "words": 0}
    store.close()


def test_search_matches_raw_or_final_and_treats_like_wildcards_literally(tmp_path):
    store = make_store(tmp_path)
    store.add("raw has 100%_ready", "Final text", ts=1)
    store.add("nothing special", "final has back\\slash", ts=2)
    store.add("100xxready", "ordinary", ts=3)

    assert [entry.ts for entry in store.search("100%_ready")] == [1]
    assert [entry.ts for entry in store.search("back\\slash")] == [2]
    assert [entry.ts for entry in store.search("ordinary")] == [3]
    assert store.count("100%_ready") == 1
    assert store.count("") == 3
    store.close()


def test_search_pagination_is_deterministic_for_identical_timestamps(tmp_path):
    store = make_store(tmp_path)
    for i in range(205):
        store.add(f"raw {i}", f"entry {i}", ts=10)
    first_page = store.search(limit=50)
    second_page = store.search(limit=50, offset=50)
    assert [entry.id for entry in first_page] == list(range(205, 155, -1))
    assert [entry.id for entry in second_page] == list(range(155, 105, -1))
    assert len(store.search(limit=50, offset=200)) == 5
    store.close()


def test_search_rejects_invalid_pagination(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match="limit"):
        store.search(limit=0)
    with pytest.raises(ValueError, match="offset"):
        store.search(offset=-1)
    store.close()


def test_delete_before_requires_reviewed_stable_count(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = HistoryStore(path)
    store.add("old one", "old one", ts=10)
    store.add("old two", "old two", ts=20)
    store.add("new", "new", ts=30)
    assert store.count_before(25) == 2

    external = sqlite3.connect(path)
    external.execute(
        "INSERT INTO entries (ts, raw_text, final_text, duration_s, word_count) "
        "VALUES (?, ?, ?, ?, ?)", (5, "new old", "new old", 0, 2))
    external.commit()
    external.close()

    with pytest.raises(ValueError, match="changed after review"):
        store.delete_before(25, expected_count=2)
    assert store.count_before(25) == 3
    assert store.delete_before(25, expected_count=3) == 3
    assert [entry.raw_text for entry in store.recent()] == ["new"]
    store.close()
