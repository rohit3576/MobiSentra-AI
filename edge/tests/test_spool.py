"""Spool queue tests (Phase 7, Step 7.1a).

The losslessness backbone under test: FIFO round-trip, id-dedupe that
survives sends, bounded retention with counted drops, and the crash
surrogate (reopen the file and everything is still there).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mobisentra.messaging.spool import SpoolEntry, SpoolQueue


def entry(n: int) -> SpoolEntry:
    event_id = f"evt-{n:03d}"
    return SpoolEntry(id=event_id, topic="mobisentra/events", payload=f'{{"id": "{event_id}"}}')


def fresh(tmp_path: Path, **kwargs) -> SpoolQueue:
    return SpoolQueue(tmp_path / "edge.db", **kwargs)


class TestRoundTrip:
    def test_enqueue_pending_mark_sent_fifo(self, tmp_path: Path):
        spool = fresh(tmp_path)
        for n in range(3):
            assert spool.enqueue(entry(n)) is True
        pending = spool.pending()
        assert [e.id for e in pending] == ["evt-000", "evt-001", "evt-002"]
        assert all(e.topic == "mobisentra/events" for e in pending)
        assert pending[0].payload == '{"id": "evt-000"}'
        spool.mark_sent(["evt-000", "evt-001", "evt-002"])
        assert spool.pending() == []
        stats = spool.stats()
        assert (stats.pending, stats.total) == (0, 3)

    def test_pending_respects_batch_size(self, tmp_path: Path):
        spool = fresh(tmp_path)
        for n in range(5):
            spool.enqueue(entry(n))
        assert [e.id for e in spool.pending(batch=2)] == ["evt-000", "evt-001"]
        assert [e.id for e in spool.pending(batch=500)][:3] == ["evt-000", "evt-001", "evt-002"]

    def test_fifo_continues_after_partial_send(self, tmp_path: Path):
        spool = fresh(tmp_path)
        for n in range(4):
            spool.enqueue(entry(n))
        spool.mark_sent(["evt-000", "evt-001"])
        assert [e.id for e in spool.pending()] == ["evt-002", "evt-003"]
        spool.enqueue(entry(4))
        assert spool.pending()[-1].id == "evt-004"


class TestDedupe:
    def test_duplicate_id_ignored_and_counted_once(self, tmp_path: Path):
        spool = fresh(tmp_path)
        assert spool.enqueue(entry(0)) is True
        assert spool.enqueue(entry(0)) is False
        stats = spool.stats()
        assert (stats.pending, stats.total, stats.dropped) == (1, 1, 0)

    def test_dedupe_survives_send_at_least_once_replays(self, tmp_path: Path):
        spool = fresh(tmp_path)
        spool.enqueue(entry(0))
        spool.mark_sent(["evt-000"])
        assert spool.enqueue(entry(0)) is False
        assert spool.pending() == []

    def test_mark_sent_is_idempotent(self, tmp_path: Path):
        spool = fresh(tmp_path)
        spool.enqueue(entry(0))
        spool.mark_sent(["evt-000"])
        spool.mark_sent(["evt-000"])  # QoS-1 PUBACK replay must not error
        assert spool.stats().total == 1


class TestRetentionCap:
    def test_cap_drops_oldest_and_counts(self, tmp_path: Path):
        spool = fresh(tmp_path, max_entries=3)
        for n in range(5):
            spool.enqueue(entry(n))
        stats = spool.stats()
        assert (stats.total, stats.dropped) == (3, 2)
        assert [e.id for e in spool.pending()] == ["evt-002", "evt-003", "evt-004"]

    def test_cap_applies_to_sent_rows_too(self, tmp_path: Path):
        spool = fresh(tmp_path, max_entries=2)
        for n in range(2):
            spool.enqueue(entry(n))
        spool.mark_sent(["evt-000"])
        spool.enqueue(entry(2))
        stats = spool.stats()
        assert stats.total == 2
        assert [e.id for e in spool.pending()] == ["evt-001", "evt-002"]


class TestCrashSurrogate:
    def test_reopen_preserves_rows_order_and_dropped_counter(self, tmp_path: Path):
        spool = fresh(tmp_path, max_entries=3)
        for n in range(4):
            spool.enqueue(entry(n))
        spool.mark_sent(["evt-001"])
        spool.close()

        revived = fresh(tmp_path, max_entries=3)
        stats = revived.stats()
        assert (stats.pending, stats.total, stats.dropped) == (2, 3, 1)
        assert [e.id for e in revived.pending()] == ["evt-002", "evt-003"]
        revived.enqueue(entry(9))
        assert revived.pending()[-1].id == "evt-009"


class TestValidation:
    def test_empty_fields_rejected(self, tmp_path: Path):
        spool = fresh(tmp_path)
        with pytest.raises(ValueError, match="id"):
            spool.enqueue(SpoolEntry(id="", topic="t", payload="p"))
        with pytest.raises(ValueError, match="topic"):
            spool.enqueue(SpoolEntry(id="x", topic="", payload="p"))
        with pytest.raises(ValueError, match="payload"):
            spool.enqueue(SpoolEntry(id="x", topic="t", payload=""))

    def test_invalid_batch_and_cap_rejected(self, tmp_path: Path):
        spool = fresh(tmp_path)
        with pytest.raises(ValueError, match="batch"):
            spool.pending(batch=0)
        with pytest.raises(ValueError, match="max_entries"):
            fresh(tmp_path, max_entries=0)
