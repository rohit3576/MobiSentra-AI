#!/usr/bin/env python
"""Gate-7 messaging soak (Phase 7, Step 7.4b) — one-shot, never in CI.

Drives the real production path through scripted failure epochs and records
JSON evidence (``runs/messaging-soak.json``):

    steady stream → EMQX BLACKOUT (events keep spooling at the edge)
    → reconnect + backlog replay → Kafka KILL mid-stream (bridge backpressure)
    → recovery → consume Kafka back → zero-loss verdict.

Gate criteria (implementation-sequence GATE 7): a 10-minute blackout with
active events → all events arrive post-reconnect; broker kill/restore → no
crash, full replay; zero loss + zero duplicates after id-dedupe.

    cd edge && uv run python tools/messaging_soak.py            # gate (≈12 min)
    uv run python tools/messaging_soak.py --blackout-min 1 ...   # rehearsal
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mobisentra.messaging.publisher import EventPublisher  # noqa: E402
from mobisentra.messaging.spool import SpoolQueue  # noqa: E402
from mobisentra.messaging.transport_paho import PahoTransport  # noqa: E402

EMQX = "mobisentra-emqx"
KAFKA = "mobisentra-kafka"
COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.yml"


def docker(*args: str, timeout: float = 120.0) -> None:
    subprocess.run(["docker", *args], check=True, timeout=timeout, capture_output=True)


def wait_container_healthy(name: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.stdout.strip() == "healthy":
            return
        time.sleep(2.0)
    raise TimeoutError(f"{name} not healthy within {timeout_s:.0f}s")


def wait_port(port: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1.0):
                return
        except OSError:
            time.sleep(1.0)
    raise TimeoutError(f"port {port} not reachable within {timeout_s:.0f}s")


def consume_kafka_ids(run_prefix: str, timeout_ms: int = 10_000) -> list[str]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE),
            "exec",
            "-T",
            "kafka",
            "/opt/kafka/bin/kafka-console-consumer.sh",
            "--bootstrap-server",
            "localhost:9092",
            "--topic",
            "mobisentra.events",
            "--from-beginning",
            "--timeout-ms",
            str(timeout_ms),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_ms / 1000 + 30,
        check=False,
    )
    ids: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            event_id = str(json.loads(line).get("id", ""))
        except json.JSONDecodeError:
            continue
        if event_id.startswith(run_prefix):
            ids.append(event_id)
    return ids


def make_envelope(event_id: str, n: int) -> dict[str, object]:
    return {
        "specversion": "1.0",
        "id": event_id,
        "source": "/mobisentra/edge/SOAK_BUS/SOAK_CAM",
        "type": "org.mobisentra.event.fall_detected",
        "time": "2026-08-29T00:00:00Z",
        "datacontenttype": "application/json",
        "data": {
            "event_type": "fall_detected",
            "severity": "HIGH",
            "camera_id": "SOAK_CAM",
            "timestamp": "2026-08-29T00:00:00Z",
            "confidence": 0.9,
            "n": n,
        },
    }


class Soak:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.run = uuid.uuid4().hex[:8]
        self.prefix = f"soak-{self.run}-"
        self.spool = SpoolQueue(Path(f"runs/spool/soak-{self.run}.db"))
        self.transport: PahoTransport | None = None
        self.publisher: EventPublisher | None = None
        self.sent: list[str] = []
        self.timeline: list[dict[str, object]] = []
        self.t0 = time.monotonic()

    def connect(self) -> None:
        self.transport = PahoTransport(
            url=self.args.url, client_id=f"soak-{self.run}", puback_timeout_s=10.0
        )
        self.publisher = EventPublisher(
            spool=self.spool, transport=self.transport, batch=500
        )

    def emit(self, seconds: float, phase: str) -> None:
        """Publish at the configured rate for ``seconds``, draining every
        tick. This is the 'active events' stream that must survive epochs."""
        assert self.publisher is not None
        rate = self.args.rate
        interval = 1.0 / rate
        deadline = time.monotonic() + seconds
        next_send = 0.0
        last_report = 0.0
        sent_at_phase_start = len(self.sent)
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send:
                n = len(self.sent)
                event_id = f"{self.prefix}{n:05d}"
                self.publisher.publish(make_envelope(event_id, n))
                self.sent.append(event_id)
                next_send = now + interval
            self.publisher.drain_once()
            if now - last_report >= 15.0:
                last_report = now
                stats = self.spool.stats()
                print(
                    f"[{time.monotonic() - self.t0:7.1f}s] {phase:24} sent={len(self.sent):4d} "
                    f"pending={stats.pending:4d} total={stats.total}",
                    flush=True,
                )
            time.sleep(0.05)
        self.timeline.append(
            {
                "phase": phase,
                "seconds": round(seconds, 1),
                "sent": len(self.sent) - sent_at_phase_start,
                "spool": asdict(self.spool.stats()),
                "at_s": round(time.monotonic() - self.t0, 1),
            }
        )

    def settle(self, seconds: float, phase: str) -> None:
        assert self.publisher is not None
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.publisher.drain_once()
            time.sleep(0.2)
        self.timeline.append(
            {
                "phase": phase,
                "seconds": round(seconds, 1),
                "sent": 0,
                "spool": asdict(self.spool.stats()),
                "at_s": round(time.monotonic() - self.t0, 1),
            }
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blackout-min", type=float, default=10.0, help="gate value = 10")
    parser.add_argument("--steady-s", type=float, default=20.0)
    parser.add_argument("--recovery-s", type=float, default=25.0)
    parser.add_argument("--kill-s", type=float, default=15.0)
    parser.add_argument("--post-s", type=float, default=25.0)
    parser.add_argument("--rate", type=float, default=0.2, help="events/second")
    parser.add_argument("--url", default="mqtt://localhost:1883")
    parser.add_argument("--json", type=Path, default=Path("runs/messaging-soak.json"))
    args = parser.parse_args()

    soak = Soak(args)
    soak.connect()
    print(
        f"[soak] run {soak.run} — steady → blackout({args.blackout_min}min) → "
        "replay → kafka-kill → recover",
        flush=True,
    )

    soak.emit(args.steady_s, "steady")

    docker("stop", EMQX)
    soak.emit(args.blackout_min * 60.0, "BLACKOUT (emqx down)")
    docker("start", EMQX)
    wait_port(1883, timeout_s=90.0)
    soak.emit(args.recovery_s, "post-blackout replay")

    docker("stop", KAFKA)
    soak.emit(args.kill_s, "KAFKA KILL (bridge backpressure)")
    docker("start", KAFKA)
    wait_container_healthy(KAFKA, timeout_s=180.0)
    soak.emit(args.post_s, "post-kill recovery")
    soak.settle(15.0, "final settle (bridge probe + drain)")

    assert soak.transport is not None and soak.publisher is not None
    soak.transport.close()
    stats = soak.spool.stats()
    received = consume_kafka_ids(soak.prefix)
    unique_received = sorted(set(received))
    sent_set = set(soak.sent)
    loss = sorted(sent_set - set(received))
    duplicates = len(received) - len(set(received))
    unexpected = sorted(set(received) - sent_set)
    ordered = unique_received == sorted(soak.sent)

    evidence = {
        "run": soak.run,
        "config": {
            "blackout_min": args.blackout_min,
            "rate_per_s": args.rate,
            "steady_s": args.steady_s,
            "recovery_s": args.recovery_s,
            "kill_s": args.kill_s,
            "post_s": args.post_s,
        },
        "timeline": soak.timeline,
        "counts": {
            "sent": len(soak.sent),
            "received": len(received),
            "unique_received": len(unique_received),
            "duplicates": duplicates,
            "lost": len(loss),
            "spool_pending_at_end": stats.pending,
            "spool_dropped": stats.dropped,
        },
        "loss": loss,
        "unexpected": unexpected,
        "order_preserved": ordered,
        "verdict": "PASS" if not loss and not unexpected and stats.pending == 0 else "FAIL",
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(evidence, indent=2))
    print(json.dumps({k: evidence[k] for k in ("counts", "order_preserved", "verdict")}, indent=2))
    print(f"[soak] evidence -> {args.json}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
