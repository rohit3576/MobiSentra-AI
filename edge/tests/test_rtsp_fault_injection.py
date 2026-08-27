"""RTSP fault-injection test (Phase 1, plan §6.9) — gate evidence.

Requires the rtsp compose profile running:

  docker compose -f infra/docker-compose.yml --profile rtsp up -d

then execute with:

  MOBISENTRA_RTSP_FAULT_TEST=1 uv run pytest tests/test_rtsp_fault_injection.py -v -s

Scenario: reader attached to rtsp://localhost:8554/buscam reaches UP;
`docker compose stop fake-cam` kills the camera; the reader must leave UP
within 15 s (gate); `start fake-cam` restores it; the reader must recover.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from mobisentra.ingestion.sources import open_real_capture, resolve_source
from mobisentra.ingestion.stream_reader import StreamReader

RTSP_URL = "rtsp://localhost:8554/buscam"
COMPOSE_FILE = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.yml"
COMPOSE = ["docker", "compose", "-f", str(COMPOSE_FILE), "--profile", "rtsp"]

pytestmark = pytest.mark.skipif(
    os.environ.get("MOBISENTRA_RTSP_FAULT_TEST") != "1",
    reason="set MOBISENTRA_RTSP_FAULT_TEST=1 and start the rtsp compose profile",
)


def wait_until(predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


@pytest.fixture()
def reader():
    spec = resolve_source(RTSP_URL)
    r = StreamReader(
        "RTSP_FAKCAM",
        spec,
        open_capture=open_real_capture,
        backoff_initial_s=1.0,
        backoff_max_s=4.0,
    )
    r.start()
    yield r
    r.stop()
    subprocess.run([*COMPOSE, "start", "fake-cam"], check=False, capture_output=True)


def test_camera_kill_and_restore(reader: StreamReader):
    assert wait_until(lambda: reader.status().state == "UP", timeout_s=30), (
        "reader never reached UP — is the rtsp compose profile running?"
    )
    frame = reader.get_frame(timeout_s=5.0)
    assert frame is not None

    down = subprocess.run([*COMPOSE, "stop", "fake-cam"], check=True, capture_output=True)
    assert down.returncode == 0
    t_kill = time.monotonic()
    left_up = wait_until(lambda: reader.status().state in ("DOWN", "RECONNECTING"), timeout_s=15.0)
    assert left_up, "reader stayed UP >15 s after camera death (gate violation)"
    print(f"\n[fault] left UP after {time.monotonic() - t_kill:.1f}s")

    subprocess.run([*COMPOSE, "start", "fake-cam"], check=True, capture_output=True)
    recovered = wait_until(lambda: reader.status().state == "UP", timeout_s=30.0)
    assert recovered, "reader did not recover after camera restore"
    frame = reader.get_frame(timeout_s=10.0)
    assert frame is not None
    stats = reader.status()
    assert stats.reconnects >= 1
    print(f"\n[fault] recovered; reconnects={stats.reconnects} frames_read={stats.frames_read}")
