from orpheus.history import HistoryStore


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
