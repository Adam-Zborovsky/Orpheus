"""Local transcript history in SQLite."""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    raw_text TEXT NOT NULL,
    final_text TEXT NOT NULL,
    duration_s REAL NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class HistoryEntry:
    id: int
    ts: float
    raw_text: str
    final_text: str
    duration_s: float
    word_count: int


class HistoryStore:
    """SQLite-backed history; safe to call from worker threads."""

    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def add(self, raw_text: str, final_text: str, duration_s: float = 0.0,
            ts: float | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO entries (ts, raw_text, final_text, duration_s, word_count)"
                " VALUES (?, ?, ?, ?, ?)",
                (ts if ts is not None else time.time(), raw_text, final_text,
                 duration_s, len(final_text.split())),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent(self, limit: int = 50) -> list[HistoryEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, raw_text, final_text, duration_s, word_count"
                " FROM entries ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [HistoryEntry(*row) for row in rows]

    @staticmethod
    def _search_pattern(query: str) -> str:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def search(self, query: str = "", limit: int = 50,
               offset: int = 0) -> list[HistoryEntry]:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        pattern = self._search_pattern(query)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, raw_text, final_text, duration_s, word_count "
                "FROM entries WHERE raw_text LIKE ? ESCAPE '\\' "
                "OR final_text LIKE ? ESCAPE '\\' "
                "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                (pattern, pattern, limit, offset),
            ).fetchall()
        return [HistoryEntry(*row) for row in rows]

    def count(self, query: str = "") -> int:
        pattern = self._search_pattern(query)
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM entries "
                "WHERE raw_text LIKE ? ESCAPE '\\' "
                "OR final_text LIKE ? ESCAPE '\\'",
                (pattern, pattern),
            ).fetchone()
        return int(row[0])

    def count_before(self, cutoff: float) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM entries WHERE ts < ?", (cutoff,),
            ).fetchone()
        return int(row[0])

    def delete_before(self, cutoff: float, expected_count: int) -> int:
        """Delete only the reviewed older-entry count, in one write transaction."""
        if type(expected_count) is not int or expected_count < 0:
            raise ValueError("expected_count must be a non-negative integer")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM entries WHERE ts < ?", (cutoff,),
                ).fetchone()
                current_count = int(row[0])
                if current_count != expected_count:
                    raise ValueError(
                        "Older history changed after review; review the count again"
                    )
                cursor = self._conn.execute(
                    "DELETE FROM entries WHERE ts < ?", (cutoff,),
                )
                deleted = int(cursor.rowcount)
                if deleted != expected_count:
                    raise ValueError(
                        "Older history changed during deletion; nothing was deleted"
                    )
                self._conn.commit()
                return deleted
            except Exception:
                self._conn.rollback()
                raise

    def stats(self) -> dict:
        with self._lock:
            entries, words = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(word_count), 0) FROM entries"
            ).fetchone()
        return {"entries": int(entries), "words": int(words)}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
