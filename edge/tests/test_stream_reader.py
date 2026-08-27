"""StreamReader tests with fakes (Phase 1, plan §6.2–6.7).

Real cv2 interaction is covered by the synthetic-video test at the bottom;
everything else runs against FakeCapture/FakeClock with no I/O.
"""

from __future__ import annotations

import time as timemod

from fakes import FakeCapture, FakeClock

from mobisentra.ingestion.sources import SourceSpec
from mobisentra.ingestion.stream_reader import StreamReader

PACED = SourceSpec("sample", "fake.mp4", True)
LIVE = SourceSpec("rtsp", "rtsp://fake/stream", False)


def make_reader(spec, factory, clock=None, **kwargs) -> StreamReader:
    return StreamReader("CAM_T", spec, open_capture=factory, clock=clock, **kwargs)


def wait_until(predicate, timeout_s: float = 5.0) -> bool:
    deadline = timemod.monotonic() + timeout_s
    while timemod.monotonic() < deadline:
        if predicate():
            return True
        timemod.sleep(0.01)
    return False


def test_frames_flow_and_state_up():
    reader = make_reader(PACED, lambda spec: FakeCapture(frames=10, fps=1000.0))
    reader.start()
    try:
        assert wait_until(lambda: reader.status().state == "UP")
        frame = reader.get_frame(timeout_s=2.0)
        assert frame is not None
        assert frame.source_id == "CAM_T"
        assert frame.frame_index >= 1
        assert frame.image is not None
    finally:
        reader.stop()


def test_latest_frame_only_drops_stale_frames():
    reader = make_reader(PACED, lambda spec: FakeCapture(frames=5, fps=1000.0))
    reader.start()
    try:
        assert wait_until(lambda: reader.status().frames_read >= 10)
        frame = reader.get_frame(timeout_s=2.0)
        assert frame is not None
        stats = reader.status()
        assert stats.frames_fetched == 1
        assert stats.frames_read - stats.frames_fetched >= 9
    finally:
        reader.stop()


def test_eof_loops_and_keeps_counting():
    reader = make_reader(PACED, lambda spec: FakeCapture(frames=4, fps=1000.0))
    reader.start()
    try:
        assert wait_until(lambda: reader.status().frames_read >= 4)
        first = reader.status().frames_read
        assert wait_until(lambda: reader.status().frames_read > first + 4)
    finally:
        reader.stop()


def test_pacing_follows_native_fps(fake_clock):
    reader = make_reader(
        PACED, lambda spec: FakeCapture(frames=50, fps=25.0, dies_after=50), clock=fake_clock
    )
    reader.start()
    try:
        assert wait_until(lambda: reader.status().frames_read >= 50)
    finally:
        reader.stop()
    pacing_sleeps = [s for s in fake_clock.sleeps if s > 0.02]
    total_sleep = sum(pacing_sleeps)
    assert 1.5 <= total_sleep <= 2.5


def test_live_death_marks_down_and_reconnects():
    holder = {"captures": []}

    def factory(spec):
        if not holder["captures"]:
            holder["captures"].append(FakeCapture(frames=3, fps=1000.0, dies_after=3))
        else:
            holder["captures"].append(FakeCapture(frames=5, fps=1000.0))
        return holder["captures"][-1]

    reader = make_reader(LIVE, factory, clock=FakeClock())
    reader.start()
    try:
        assert wait_until(lambda: reader.status().frames_read >= 3)
        assert wait_until(lambda: reader.status().reconnects >= 1)
        assert wait_until(lambda: reader.status().state == "UP")
        assert wait_until(lambda: reader.status().frames_read > 3)
    finally:
        reader.stop()


def test_backoff_sequence_exponential_capped(fake_clock):
    dead = FakeCapture(dies_after=0)
    reader = make_reader(
        LIVE,
        lambda spec: dead,
        clock=fake_clock,
        backoff_initial_s=1.0,
        backoff_max_s=60.0,
    )
    reader.start()
    try:
        assert wait_until(lambda: len(fake_clock.sleeps) >= 8)
    finally:
        reader.stop()
    live_backoffs = [s for s in fake_clock.sleeps if s >= 1.0]
    assert live_backoffs[:8] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


def test_open_failure_backoff_then_success(fake_clock):
    attempts = {"n": 0}

    def factory(spec):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            capture = FakeCapture()
            capture.released = True
            return capture
        return FakeCapture(frames=3, fps=1000.0)

    reader = make_reader(PACED, factory, clock=fake_clock, backoff_initial_s=1.0, backoff_max_s=4.0)
    reader.start()
    try:
        assert wait_until(lambda: reader.status().state == "UP")
        open_backoffs = [s for s in fake_clock.sleeps if s in (1.0, 2.0)]
        assert open_backoffs[:2] == [1.0, 2.0]
    finally:
        reader.stop()


def test_get_frame_timeout_returns_none():
    reader = make_reader(PACED, lambda spec: FakeCapture(frames=2))
    started = timemod.monotonic()
    assert reader.get_frame(timeout_s=0.05) is None
    assert timemod.monotonic() - started < 1.0


def test_stop_is_idempotent_and_sets_stopped():
    reader = make_reader(PACED, lambda spec: FakeCapture(frames=3))
    reader.start()
    assert wait_until(lambda: reader.status().frames_read >= 1)
    reader.stop()
    reader.stop()
    assert reader.status().state == "STOPPED"
    assert reader._thread is not None and not reader._thread.is_alive()


def test_real_video_file_roundtrip(synthetic_video):
    from mobisentra.ingestion.sources import open_real_capture, resolve_source

    spec = resolve_source(f"file://{synthetic_video}")
    reader = make_reader(spec, open_real_capture)
    reader.start()
    try:
        frame = reader.get_frame(timeout_s=5.0)
        assert frame is not None
        assert frame.image.shape == (48, 64, 3)
        assert frame.image.mean() > 0
    finally:
        reader.stop()
