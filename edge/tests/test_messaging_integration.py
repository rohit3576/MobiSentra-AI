"""Real-stack messaging integration (Phase 7, Step 7.2).

Full round trip through production pieces: ``PahoTransport`` → EMQX → the
Phase-0 bridge → Kafka ``mobisentra.events``, consumed back via the kafka
console consumer (docker exec — the project's documented smoke pattern,
zero new Python deps). Auto-skips with instructions when the dev stack is
down; the skip gate itself is unit-tested so the skip path is proven.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from mobisentra.messaging.publisher import EventPublisher
from mobisentra.messaging.spool import SpoolQueue
from mobisentra.messaging.transport_paho import PahoTransport

COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.yml"
STACK_INSTRUCTIONS = "dev stack not running: docker compose -f infra/docker-compose.yml up -d"


def emqx_reachable(host: str = "localhost", port: int = 1883, timeout_s: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def kafka_container_running(compose: Path = COMPOSE) -> bool:
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose), "ps", "--format", "json", "kafka"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    try:
        states = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if isinstance(states, dict):
        states = [states]
    return any(str(entry.get("State", "")).lower() == "running" for entry in states)


stack_up = emqx_reachable() and kafka_container_running()
pytestmark = pytest.mark.skipif(not stack_up, reason=STACK_INSTRUCTIONS)


def consume_kafka_lines(timeout_ms: int = 8000) -> list[str]:
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
    return [line for line in result.stdout.splitlines() if line.strip()]


def make_envelope(event_id: str) -> dict[str, object]:
    return {
        "specversion": "1.0",
        "id": event_id,
        "source": "/mobisentra/edge/IT_BUS/IT_CAM",
        "type": "org.mobisentra.event.fall_detected",
        "time": "2026-08-29T00:00:00Z",
        "datacontenttype": "application/json",
        "data": {
            "event_type": "fall_detected",
            "severity": "HIGH",
            "camera_id": "IT_CAM",
            "timestamp": "2026-08-29T00:00:00Z",
            "confidence": 0.9,
        },
    }


def test_hundred_envelopes_round_trip_byte_intact_and_ordered(tmp_path: Path):
    run = uuid.uuid4().hex[:8]
    ids = [f"it-{run}-{i:04d}" for i in range(100)]
    envelopes = [make_envelope(event_id) for event_id in ids]
    expected_payloads = {env["id"]: json.dumps(env) for env in envelopes}

    spool = SpoolQueue(tmp_path / "it.db")
    transport = PahoTransport(
        url="mqtt://localhost:1883", client_id=f"it-{run}", puback_timeout_s=10.0
    )
    publisher = EventPublisher(spool=spool, transport=transport, batch=100)
    try:
        for envelope in envelopes:
            publisher.publish(envelope)
        deadline = time.monotonic() + 30.0
        while spool.stats().pending and time.monotonic() < deadline:
            publisher.drain_once()
        assert spool.stats().pending == 0, "drain did not clear the spool in 30 s"
    finally:
        transport.close()

    time.sleep(1.0)  # bridge forwarding settle
    received_ids: list[str] = []
    mismatches: list[str] = []
    for line in consume_kafka_lines():
        try:
            event_id = str(json.loads(line).get("id", ""))
        except json.JSONDecodeError:
            continue
        if event_id.startswith(f"it-{run}-"):
            received_ids.append(event_id)
            if line != expected_payloads[event_id]:
                mismatches.append(event_id)

    assert received_ids == ids, f"expected {len(ids)} in order, got {len(received_ids)}"
    assert mismatches == [], f"payload bytes diverged for {mismatches}"


def test_skip_gate_detects_dead_endpoints():
    assert emqx_reachable(host="localhost", port=1) is False  # nothing listens on :1
    assert kafka_container_running(compose=COMPOSE.parent / "no-such-compose.yml") is False
