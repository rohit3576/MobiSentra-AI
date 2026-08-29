"""MQTT event publisher — write-ahead, replay, never blocks (Phase 7, 7.1b).

The pipeline-facing contract is one fast local call: ``publish(envelope)``
persists the envelope JSON into the :class:`SpoolQueue` (dedupe on the
CloudEvents ``id``) and returns. Everything network-shaped lives in
``drain_once`` passes: pending entries FIFO → transport ``deliver`` (QoS 1
semantics: PUBACK inside the call, any failure raises) → batched
``mark_sent``. A failed pass retains every undelivered row; the next pass
replays them in order — at-least-once delivery, deduped server-side
(Phase 8) and at re-enqueue (the spool refuses known ids forever).

The transport is injected: paho v2 in production (adapter in 7.1c), a stub
in unit tests. ``start()`` runs the drain loop on a daemon thread woken by
publishes and backing off exponentially on failures; ``drain_once`` stays
public so tests and the soak tool drive it deterministically without
threads.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from mobisentra.messaging.spool import SpoolEntry, SpoolQueue, SpoolStats

DEFAULT_TOPIC = "mobisentra/events"
DEFAULT_BATCH = 500
DEFAULT_BACKOFF_INITIAL_S = 1.0
DEFAULT_BACKOFF_MAX_S = 60.0
IDLE_POLL_S = 0.5


class Transport(Protocol):
    """Synchronous QoS-1 delivery: return = PUBACKed, raise = not delivered."""

    def deliver(self, topic: str, payload: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DrainResult:
    sent: int
    ok: bool


def next_backoff(failures: int, initial_s: float, max_s: float) -> float:
    """Exponential retry delay: 1st failure waits ``initial_s``, doubling
    per consecutive failure, capped at ``max_s``."""
    if failures < 1:
        return 0.0
    return min(initial_s * 2 ** (failures - 1), max_s)


class EventPublisher:
    """Write-ahead publisher over :class:`SpoolQueue` + an MQTT transport."""

    def __init__(
        self,
        *,
        spool: SpoolQueue,
        transport: Transport,
        topic: str = DEFAULT_TOPIC,
        batch: int = DEFAULT_BATCH,
        backoff_initial_s: float = DEFAULT_BACKOFF_INITIAL_S,
        backoff_max_s: float = DEFAULT_BACKOFF_MAX_S,
    ) -> None:
        if not topic:
            raise ValueError("topic must be non-empty")
        if batch < 1:
            raise ValueError(f"batch must be ≥ 1, got {batch}")
        self._spool = spool
        self._transport = transport
        self._topic = topic
        self._batch = batch
        self._backoff_initial_s = backoff_initial_s
        self._backoff_max_s = backoff_max_s
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def topic(self) -> str:
        return self._topic

    def publish(self, envelope: Mapping[str, object]) -> bool:
        """Persist one envelope before any network attempt (the only
        pipeline-facing call; fast + local). False = duplicate id, not
        re-queued (counted no-op)."""
        event_id = str(envelope.get("id", ""))
        if not event_id:
            raise ValueError("envelope must carry a non-empty CloudEvents 'id'")
        inserted = self._spool.enqueue(
            SpoolEntry(id=event_id, topic=self._topic, payload=json.dumps(envelope))
        )
        if inserted:
            self._wakeup.set()
        return inserted

    def drain_once(self) -> DrainResult:
        """One replay pass: pending FIFO → deliver → batched mark_sent.
        Stops at the first delivery failure (broker down fails them all);
        delivered-so-far rows are still marked sent."""
        entries = self._spool.pending(self._batch)
        if not entries:
            return DrainResult(sent=0, ok=True)
        acked: list[str] = []
        for entry in entries:
            try:
                self._transport.deliver(entry.topic, entry.payload)
            except Exception:
                break
            acked.append(entry.id)
        if acked:
            self._spool.mark_sent(acked)
        return DrainResult(sent=len(acked), ok=len(acked) == len(entries))

    def stats(self) -> SpoolStats:
        return self._spool.stats()

    def start(self) -> None:
        """Background drain loop (daemon): woken by publishes, exponential
        backoff on failures, idle poll otherwise."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="event-publisher", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        self._wakeup.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            result = self.drain_once()
            failures = 0 if result.ok else failures + 1
            if failures:
                delay = next_backoff(failures, self._backoff_initial_s, self._backoff_max_s)
            else:
                delay = IDLE_POLL_S
            self._wakeup.wait(timeout=delay)
            self._wakeup.clear()
