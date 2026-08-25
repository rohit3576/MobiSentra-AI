"""StreamReader: latest-frame-only reader thread per camera.

Design (Doc/Implementation/phase-1-plan.md §3):
- one daemon thread per camera owns the cv2 capture (OpenCV captures are
  not thread-safe); the consumer never touches it
- the thread overwrites a single lock-guarded slot with the freshest frame;
  stale frames are dropped, never queued — lag cannot accumulate even when
  inference is slower than capture
- paced file/sample sources are played at native FPS against an absolute
  schedule (t0 + n/fps) so lag and FPS metrics are meaningful; EOF loops
- live sources reconnect with exponential backoff; RTSP read-hangs are
  bounded by the capture read-timeout set in sources.open_real_capture
- capture factory and clock are injectable for fake-based unit tests
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from mobisentra.ingestion.frame import Frame
from mobisentra.ingestion.sources import SourceSpec

BACKOFF_INITIAL_S = 1.0
BACKOFF_MAX_S = 60.0
EOF_RETRY_SLEEP_S = 0.01
FALLBACK_FPS = 25.0


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


CaptureFactory = Callable[[SourceSpec], object]


@dataclass(frozen=True)
class StreamStats:
    state: str
    last_frame_ts: float | None
    frames_read: int
    frames_fetched: int
    reconnects: int


class StreamReader:
    def __init__(
        self,
        camera_id: str,
        spec: SourceSpec,
        *,
        open_capture: CaptureFactory,
        clock: Clock | None = None,
        backoff_initial_s: float = BACKOFF_INITIAL_S,
        backoff_max_s: float = BACKOFF_MAX_S,
    ) -> None:
        self._camera_id = camera_id
        self._spec = spec
        self._open_capture = open_capture
        self._clock = clock or RealClock()
        self._backoff_initial = backoff_initial_s
        self._backoff_max = backoff_max_s

        self._cond = threading.Condition()
        self._slot: Frame | None = None
        self._slot_seq = 0
        self._delivered_seq = 0

        self._state = "STARTING"
        self._last_frame_ts: float | None = None
        self._frames_read = 0
        self._frames_fetched = 0
        self._reconnects = 0

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def camera_id(self) -> str:
        return self._camera_id

    @property
    def spec(self) -> SourceSpec:
        return self._spec

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError(f"reader for {self._camera_id} already started")
        self._thread = threading.Thread(
            target=self._run, name=f"reader-{self._camera_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        with self._cond:
            self._state = "STOPPED"
            self._cond.notify_all()

    def get_frame(self, timeout_s: float | None = None) -> Frame | None:
        """Return the latest unread frame, or None on timeout.

        Blocks until a frame newer than the last delivered one is available.
        A slow consumer automatically skips intermediate frames (they were
        overwritten in the slot) and always receives the freshest one.
        """
        clock = self._clock
        deadline = None if timeout_s is None else clock.monotonic() + timeout_s
        with self._cond:
            while self._slot_seq <= self._delivered_seq:
                if deadline is None:
                    self._cond.wait(0.05)
                    continue
                remaining = deadline - clock.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(min(remaining, 0.05))
            assert self._slot is not None
            frame = self._slot
            self._delivered_seq = self._slot_seq
            self._frames_fetched += 1
            return frame

    def status(self) -> StreamStats:
        with self._cond:
            return StreamStats(
                state=self._state,
                last_frame_ts=self._last_frame_ts,
                frames_read=self._frames_read,
                frames_fetched=self._frames_fetched,
                reconnects=self._reconnects,
            )

    def _run(self) -> None:
        backoff = self._backoff_initial
        while not self._stop.is_set():
            cap = self._open_capture(self._spec)
            if cap is None or not cap.isOpened():
                with self._cond:
                    self._state = "DOWN"
                self._clock.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_max)
                continue

            frames_before = self._frames_read
            self._pump(cap)
            cap.release()
            if self._stop.is_set():
                break
            if self._frames_read > frames_before:
                backoff = self._backoff_initial
            self._reconnects += 1
            with self._cond:
                self._state = "RECONNECTING"
            self._clock.sleep(backoff)
            backoff = min(backoff * 2, self._backoff_max)
        with self._cond:
            self._state = "STOPPED"
            self._cond.notify_all()

    def _pump(self, cap) -> bool:
        """Read frames into the slot until the stream ends or stop is set.

        Returns True when the stream ended (read failure on a live source),
        False when stop was requested.
        """
        import cv2

        fps = FALLBACK_FPS
        if self._spec.paced:
            raw_fps = cap.get(cv2.CAP_PROP_FPS)
            if raw_fps and raw_fps > 0 and np.isfinite(raw_fps):
                fps = float(raw_fps)
        t0 = self._clock.monotonic()

        while not self._stop.is_set():
            ok, image = cap.read()
            if not ok:
                if self._spec.paced:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self._clock.sleep(EOF_RETRY_SLEEP_S)
                    continue
                return True

            self._frames_read += 1
            ts = self._clock.time()
            with self._cond:
                self._slot = Frame(
                    image=image,
                    capture_ts=ts,
                    frame_index=self._frames_read,
                    source_id=self._camera_id,
                )
                self._slot_seq += 1
                self._last_frame_ts = ts
                self._state = "UP"
                self._cond.notify_all()

            if self._spec.paced:
                target = t0 + self._frames_read / fps
                now = self._clock.monotonic()
                if target > now:
                    self._clock.sleep(target - now)
        return False
