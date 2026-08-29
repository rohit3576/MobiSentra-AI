"""SQLite write-ahead event spool (Phase 7, Step 7.1a).

The losslessness backbone: every envelope is persisted **before** any
network publish attempt (the publisher in 7.1b only reads from here).
One edge-wide queue — one vehicle, one spool. CloudEvents ``id`` is the
primary key, so a duplicate enqueue is a counted no-op *forever* (not just
in flight): QoS-1 redeliveries and engine replays cannot double-send.

FIFO order is the SQLite ``rowid`` (monotonic across connections and
restarts — SQLite cannot index ``rowid``, but the ``max_entries`` cap
bounds every pending scan to a few ms at drain-tick frequency, so no
sequence column is warranted). Sent rows are kept — they are the dedupe
memory and an audit trail — bounded by ``max_entries``: when the cap is
exceeded the oldest rows are dropped and counted, never silently.
Thread-safe for the paho callback thread (``check_same_thread=False`` +
a lock; WAL survives process crashes mid-write).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spool (
    id TEXT PRIMARY KEY,          -- CloudEvents id (dedupe key)
    topic TEXT NOT NULL,
    payload TEXT NOT NULL,        -- full envelope JSON
    created_at TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spool_meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
INSERT OR IGNORE INTO spool_meta(key, value) VALUES ('dropped', 0);
"""


@dataclass(frozen=True, slots=True)
class SpoolEntry:
    """One queued envelope: its CloudEvents id, MQTT topic, and JSON bytes."""

    id: str
    topic: str
    payload: str


@dataclass(frozen=True, slots=True)
class SpoolStats:
    pending: int
    total: int
    dropped: int


class SpoolQueue:
    """Persistent FIFO queue of CloudEvents envelopes (dedupe on ``id``)."""

    def __init__(self, path: Path, *, max_entries: int = 100_000) -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be ≥ 1, got {max_entries}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def enqueue(self, entry: SpoolEntry) -> bool:
        """Persist one envelope before any publish attempt. Returns True when
        a new row was written, False when the ``id`` was already known
        (duplicate — a counted no-op, rows are never re-sent)."""
        _validate(entry)
        created_at = datetime.now(UTC).isoformat()
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO spool(id, topic, payload, created_at, sent) "
                "VALUES (?, ?, ?, ?, 0)",
                (entry.id, entry.topic, entry.payload, created_at),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                self._enforce_cap()
        return inserted

    def pending(self, batch: int = 500) -> list[SpoolEntry]:
        """Up to ``batch`` unsent entries in FIFO order (rowid = arrival)."""
        if batch < 1:
            raise ValueError(f"batch must be ≥ 1, got {batch}")
        with self._lock:
            rows = self._db.execute(
                "SELECT id, topic, payload FROM spool WHERE sent = 0 "
                "ORDER BY rowid LIMIT ?",
                (batch,),
            ).fetchall()
        return [SpoolEntry(id=row[0], topic=row[1], payload=row[2]) for row in rows]

    def mark_sent(self, ids: Iterable[str]) -> None:
        """Flag entries as delivered (idempotent). Called after MQTT PUBACK."""
        id_list = list(ids)
        if not id_list:
            return
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE spool SET sent = 1 WHERE id = ?", [(id_,) for id_ in id_list]
            )

    def stats(self) -> SpoolStats:
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
            pending = self._db.execute(
                "SELECT COUNT(*) FROM spool WHERE sent = 0"
            ).fetchone()[0]
            dropped = self._db.execute(
                "SELECT value FROM spool_meta WHERE key = 'dropped'"
            ).fetchone()[0]
        return SpoolStats(pending=pending, total=total, dropped=dropped)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _enforce_cap(self) -> None:
        """Caller holds the lock + transaction. Drops the oldest rows beyond
        ``max_entries`` (sent rows first in line simply because they are
        oldest) and counts every drop."""
        overflow = self._db.execute("SELECT COUNT(*) FROM spool").fetchone()[0]
        overflow -= self._max_entries
        if overflow <= 0:
            return
        cursor = self._db.execute(
            "DELETE FROM spool WHERE rowid IN "
            "(SELECT rowid FROM spool ORDER BY rowid LIMIT ?)",
            (overflow,),
        )
        self._db.execute(
            "UPDATE spool_meta SET value = value + ? WHERE key = 'dropped'",
            (cursor.rowcount,),
        )


def _validate(entry: SpoolEntry) -> None:
    if not entry.id:
        raise ValueError("spool entry id must be non-empty")
    if not entry.topic:
        raise ValueError("spool entry topic must be non-empty")
    if not entry.payload:
        raise ValueError("spool entry payload must be non-empty")
