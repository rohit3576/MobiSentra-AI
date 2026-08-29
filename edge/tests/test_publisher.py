"""Publisher tests (Phase 7, Step 7.1b).

Stub transport, no broker, no wall-clock sleeps beyond a bounded wait for
the one background-loop smoke test. The plan's done-when list maps 1:1 to
the test names.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mobisentra.messaging.publisher import EventPublisher, next_backoff
from mobisentra.messaging.spool import SpoolQueue


class StubTransport:
    """Records successful deliveries only (a failed attempt never hit the
    wire). ``down`` fails everything; ``fail_after`` lets the first N
    deliveries through, then fails — the broker-dies-mid-batch shape."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.down = False
        self.fail_after: int | None = None

    def deliver(self, topic: str, payload: str) -> None:
        if self.down or (self.fail_after is not None and len(self.calls) >= self.fail_after):
            raise ConnectionError("broker unreachable")
        self.calls.append((topic, payload))


def envelope(n: int) -> dict[str, object]:
    return {"id": f"env-{n:03d}", "type": "org.mobisentra.event.fall_detected", "data": {"n": n}}


@pytest.fixture
def rig(tmp_path: Path) -> tuple[EventPublisher, StubTransport, SpoolQueue]:
    transport = StubTransport()
    spool = SpoolQueue(tmp_path / "edge.db")
    publisher = EventPublisher(spool=spool, transport=transport)
    return publisher, transport, spool


def test_publish_fail_retains_row(rig):
    publisher, transport, spool = rig
    transport.down = True
    assert publisher.publish(envelope(0)) is True
    result = publisher.drain_once()
    assert (result.sent, result.ok) == (0, False)
    assert transport.calls == []  # nothing hit the wire
    assert spool.pending() != []  # row retained for replay


def test_reconnect_replays_exactly_once(rig):
    publisher, transport, spool = rig
    publisher.publish(envelope(0))
    transport.fail_after = 0
    first = publisher.drain_once()
    assert (first.sent, first.ok) == (0, False)
    transport.fail_after = None
    second = publisher.drain_once()
    assert (second.sent, second.ok) == (1, True)
    for _ in range(3):  # PUBACKed ids are never re-sent
        publisher.drain_once()
    assert len(transport.calls) == 1
    assert spool.pending() == []


def test_duplicate_enqueue_sends_once(rig):
    publisher, transport, _ = rig
    assert publisher.publish(envelope(0)) is True
    assert publisher.publish(envelope(0)) is False
    publisher.drain_once()
    publisher.drain_once()
    assert len(transport.calls) == 1


def test_payload_is_byte_intact_envelope_json(rig):
    publisher, transport, _ = rig
    publisher.publish(envelope(7))
    publisher.drain_once()
    [(topic, payload)] = transport.calls
    assert topic == "mobisentra/events"
    assert payload == json.dumps(envelope(7))
    assert json.loads(payload)["id"] == "env-007"


def test_partial_batch_marks_delivered_and_retains_rest(rig):
    publisher, transport, spool = rig
    for n in range(3):
        publisher.publish(envelope(n))
    transport.fail_after = 1  # deliver #1 ok, broker dies, pass stops
    result = publisher.drain_once()
    assert (result.sent, result.ok) == (1, False)
    assert [entry.id for entry in spool.pending()] == ["env-001", "env-002"]
    transport.fail_after = None
    assert publisher.drain_once().ok is True
    assert len(transport.calls) == 3


def test_batch_parameter_limits_pass(tmp_path):
    transport = StubTransport()
    spool = SpoolQueue(tmp_path / "edge.db")
    publisher = EventPublisher(spool=spool, transport=transport, batch=2)
    for n in range(5):
        publisher.publish(envelope(n))
    assert publisher.drain_once().sent == 2
    assert publisher.drain_once().sent == 2
    assert publisher.drain_once().sent == 1
    assert publisher.drain_once().sent == 0


def test_empty_spool_drain_is_ok_noop(rig):
    publisher, _, _ = rig
    assert publisher.drain_once() == publisher.drain_once()  # both ok, sent 0
    assert publisher.drain_once().sent == 0


def test_backoff_progression_doubles_then_caps():
    delays = [next_backoff(n, 1.0, 60.0) for n in range(1, 9)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert next_backoff(0, 1.0, 60.0) == 0.0


def test_publish_requires_envelope_id(rig):
    publisher, _, _ = rig
    with pytest.raises(ValueError, match="id"):
        publisher.publish({"type": "org.mobisentra.event.fall_detected"})
    with pytest.raises(ValueError, match="id"):
        publisher.publish({"id": ""})


def test_constructor_validation(tmp_path):
    transport = StubTransport()
    spool = SpoolQueue(tmp_path / "edge.db")
    with pytest.raises(ValueError, match="topic"):
        EventPublisher(spool=spool, transport=transport, topic="")
    with pytest.raises(ValueError, match="batch"):
        EventPublisher(spool=spool, transport=transport, batch=0)


def test_background_loop_drains_without_explicit_calls(rig):
    publisher, transport, _ = rig
    publisher.start()
    try:
        for n in range(4):
            publisher.publish(envelope(n))
        deadline = time.monotonic() + 2.0
        while len(transport.calls) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(transport.calls) == 4
    finally:
        publisher.stop()
    assert publisher.stats().pending == 0
