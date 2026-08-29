"""Fault-injection resilience tests (Phase 7, Step 7.4a).

The runbook's three scenarios at the edge layer — seconds, no real broker,
no sleeps: (a) broker kill mid-stream, (b) network partition during active
events, (c) forced duplicate delivery (QoS 1 PUBACK loss) → wire is
at-least-once, spool is exactly-once. Plus the crash-recovery narrative
(spool survives process death; the backlog replays from a fresh publisher).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mobisentra.messaging.publisher import EventPublisher
from mobisentra.messaging.spool import SpoolQueue


class FlakyTransport:
    """Scriptable MQTT transport: ``down`` epochs (connection failures) and
    ``puback_lost_for`` — ids whose delivery reaches the broker (recorded!)
    but whose PUBACK never comes back, forcing the at-least-once redelivery
    shape."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.down = False
        self.puback_lost_for: set[str] = set()

    def deliver(self, topic: str, payload: str) -> None:
        if self.down:
            raise ConnectionError("broker unreachable")
        self.calls.append((topic, payload))
        event_id = str(json.loads(payload).get("id", ""))
        if event_id in self.puback_lost_for:
            self.puback_lost_for.discard(event_id)
            raise TimeoutError(f"PUBACK not received for {event_id}")


def envelope(n: int) -> dict[str, object]:
    return {"id": f"fi-{n:03d}", "type": "org.mobisentra.event.fall_detected", "n": n}


def wire_ids(transport: FlakyTransport) -> list[str]:
    return [str(json.loads(payload)["id"]) for _, payload in transport.calls]


@pytest.fixture
def rig(tmp_path: Path) -> tuple[EventPublisher, FlakyTransport, SpoolQueue]:
    transport = FlakyTransport()
    spool = SpoolQueue(tmp_path / "edge.db")
    return EventPublisher(spool=spool, transport=transport, batch=100), transport, spool


def test_broker_kill_midstream_no_crash_rows_retained(rig):
    publisher, transport, spool = rig
    for n in range(3):
        publisher.publish(envelope(n))
    assert publisher.drain_once().ok is True

    transport.down = True  # broker killed mid-stream
    for n in range(3, 6):
        publisher.publish(envelope(n))  # pipeline keeps calling publish — no crash
    for _ in range(3):  # drain attempts during the outage (backoff ticks)
        result = publisher.drain_once()
        assert (result.sent, result.ok) == (0, False)
    assert wire_ids(transport) == ["fi-000", "fi-001", "fi-002"]  # nothing new hit the wire
    assert spool.stats().pending == 3  # rows retained

    transport.down = False
    assert publisher.drain_once().ok is True
    assert wire_ids(transport) == [f"fi-{n:03d}" for n in range(6)]  # replayed in order
    assert spool.stats().pending == 0


def test_partition_backlog_accumulates_then_drains_fifo(rig):
    publisher, transport, _ = rig
    transport.down = True  # partition from t=0 — the lazy-connect blackout path
    for batch in range(3):
        for n in range(batch * 3, batch * 3 + 3):
            publisher.publish(envelope(n))
        assert publisher.drain_once().sent == 0  # failed tick between batches
    assert wire_ids(transport) == []  # zero leakage during the partition
    assert publisher.stats().pending == 9

    transport.down = False  # reconnect
    assert publisher.drain_once().ok is True
    assert wire_ids(transport) == [f"fi-{n:03d}" for n in range(9)]  # strict FIFO
    assert publisher.drain_once().sent == 0  # backlog fully cleared


def test_puback_lost_wire_at_least_once_spool_exactly_once(rig):
    publisher, transport, spool = rig
    transport.puback_lost_for = {"fi-000"}  # broker gets it; the ack dies
    publisher.publish(envelope(0))

    first = publisher.drain_once()
    assert (first.sent, first.ok) == (0, False)  # timeout → not marked sent
    assert wire_ids(transport) == ["fi-000"]  # ...but the broker DID receive it
    assert spool.stats().pending == 1

    second = publisher.drain_once()  # QoS-1 redelivery
    assert (second.sent, second.ok) == (1, True)
    assert wire_ids(transport) == ["fi-000", "fi-000"]  # wire: at-least-once (2 sends)
    assert spool.stats().pending == 0

    assert publisher.drain_once().sent == 0  # no third send once acked
    assert publisher.publish(envelope(0)) is False  # re-enqueue refused (Phase-8 dedupe's anchor)
    assert publisher.drain_once().sent == 0
    assert wire_ids(transport) == ["fi-000", "fi-000"]
    stats = spool.stats()
    assert (stats.pending, stats.total) == (0, 1)  # spool: exactly one record ever


def test_crash_recovery_backlog_survives_process_death(tmp_path: Path):
    transport_a = FlakyTransport()
    transport_a.down = True  # partition while the process is alive
    spool_a = SpoolQueue(tmp_path / "edge.db")
    publisher_a = EventPublisher(spool=spool_a, transport=transport_a, batch=100)
    for n in range(5):
        publisher_a.publish(envelope(n))
    publisher_a.drain_once()
    spool_a.close()  # process death (no graceful stop, no transport close)

    spool_b = SpoolQueue(tmp_path / "edge.db")  # fresh process, same disk
    transport_b = FlakyTransport()
    publisher_b = EventPublisher(spool=spool_b, transport=transport_b, batch=100)
    assert publisher_b.stats().pending == 5
    assert publisher_b.drain_once().ok is True  # backlog replays from disk
    assert wire_ids(transport_b) == [f"fi-{n:03d}" for n in range(5)]
    assert [entry.id for entry in spool_b.pending()] == []


def test_partial_delivery_marks_only_acked(rig):
    """Broker dying mid-batch must never lose the delivered-so-far acks."""
    publisher, transport, spool = rig
    for n in range(4):
        publisher.publish(envelope(n))
    transport.puback_lost_for = {"fi-001"}  # e0 acks, e1's ack dies, pass stops
    first = publisher.drain_once()
    assert (first.sent, first.ok) == (1, False)
    assert wire_ids(transport) == ["fi-000", "fi-001"]
    second = publisher.drain_once()  # e1 redelivered + rest follow
    assert (second.sent, second.ok) == (3, True)
    assert wire_ids(transport) == ["fi-000", "fi-001"] + [f"fi-{n:03d}" for n in range(1, 4)]
    assert spool.stats().pending == 0
