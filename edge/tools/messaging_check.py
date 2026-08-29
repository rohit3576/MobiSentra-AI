#!/usr/bin/env python
"""One-command messaging round-trip check (Phase 7, Step 7.2).

Publishes N envelopes through the production path (EventPublisher +
PahoTransport → EMQX → bridge → Kafka), consumes the topic back, and
prints a verdict. Manual companion to the gated integration test:

    cd edge && uv run python tools/messaging_check.py [--count 20]

Requires the dev stack: docker compose -f infra/docker-compose.yml up -d
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.yml"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mobisentra.messaging.publisher import EventPublisher  # noqa: E402
from mobisentra.messaging.spool import SpoolQueue  # noqa: E402
from mobisentra.messaging.transport_paho import PahoTransport  # noqa: E402

TOPIC = "mobisentra.events"


def consume_lines(timeout_ms: int) -> list[str]:
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
            TOPIC,
            "--from-beginning",
            "--timeout-ms",
            str(timeout_ms),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_ms / 1000 + 30,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--url", default="mqtt://localhost:1883")
    args = parser.parse_args()

    run = uuid.uuid4().hex[:8]
    ids = [f"chk-{run}-{i:04d}" for i in range(args.count)]
    envelopes = [
        {
            "specversion": "1.0",
            "id": event_id,
            "source": "/mobisentra/edge/CHECK_BUS/CHECK_CAM",
            "type": "org.mobisentra.event.fall_detected",
            "time": "2026-08-29T00:00:00Z",
            "datacontenttype": "application/json",
            "data": {
                "event_type": "fall_detected",
                "severity": "HIGH",
                "camera_id": "CHECK_CAM",
                "timestamp": "2026-08-29T00:00:00Z",
                "confidence": 0.9,
            },
        }
        for event_id in ids
    ]

    spool_path = Path(f"runs/spool/check-{run}.db")
    spool = SpoolQueue(spool_path)
    transport = PahoTransport(url=args.url, client_id=f"check-{run}")
    publisher = EventPublisher(spool=spool, transport=transport, batch=args.count)
    started = time.monotonic()
    try:
        for envelope in envelopes:
            publisher.publish(envelope)
        while spool.stats().pending and time.monotonic() - started < 30:
            publisher.drain_once()
    finally:
        transport.close()
    publish_s = time.monotonic() - started

    time.sleep(1.0)
    prefix = f"chk-{run}-"
    received = [
        line
        for line in consume_lines(8000)
        if str(json.loads(line).get("id", "")).startswith(prefix)
    ]
    received_ids = [str(json.loads(line)["id"]) for line in received]
    byte_intact = [line == json.dumps(env) for line, env in zip(received, envelopes, strict=False)]
    elapsed = time.monotonic() - started

    print(f"\nrun {run}: sent {len(ids)} → received {len(received_ids)} in {elapsed:.1f}s")
    print(f"in order:      {received_ids == ids}")
    print(f"byte-intact:   {all(byte_intact) and len(byte_intact) == len(ids)}")
    print(f"publish drain: {publish_s:.1f}s (spool pending now: {spool.stats().pending})")
    verdict = received_ids == ids and all(byte_intact) and len(byte_intact) == len(ids)
    print("VERDICT:", "PASS" if verdict else "FAIL")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
