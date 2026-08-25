"""Test fakes: controllable capture devices and a virtual clock."""

from __future__ import annotations

import numpy as np

POS_FRAMES = 1
CAP_PROP_FPS = 5


def make_image(value: int, size: tuple[int, int] = (48, 64)) -> np.ndarray:
    return np.full((*size, 3), value % 256, dtype=np.uint8)


class FakeCapture:
    """Mimics cv2.VideoCapture with deterministic behavior.

    - frames: pixel value encodes the frame index (value = 10 * position)
    - dies_after: read() fails permanently once reached (live-death path)
    - honors CAP_PROP_POS_FRAMES reset (real file-capture EOF behavior)
    """

    def __init__(
        self,
        frames: int = 5,
        *,
        fps: float = 25.0,
        dies_after: int | None = None,
        broken_reset: bool = False,
    ) -> None:
        self.frame_count = frames
        self.fps = fps
        self.dies_after = dies_after
        self.broken_reset = broken_reset
        self.pos = 0
        self.released = False
        self.reads = 0

    def isOpened(self) -> bool:
        return not self.released

    def read(self):
        self.reads += 1
        if self.dies_after is not None and self.reads > self.dies_after:
            return False, None
        if self.pos >= self.frame_count:
            return False, None
        image = make_image(10 * self.pos)
        self.pos += 1
        return True, image

    def get(self, prop: int) -> float:
        if prop == CAP_PROP_FPS:
            return self.fps
        return 0.0

    def set(self, prop: int, value: float) -> bool:
        if prop == POS_FRAMES and value == 0:
            if self.broken_reset:
                return True
            self.pos = 0
            return True
        return False

    def release(self) -> None:
        self.released = True


class FakeClock:
    """Virtual clock: sleep() advances time instantly and is logged."""

    def __init__(self) -> None:
        self._mono = 1_000_000.0
        self._wall = 1_800_000_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._wall

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._mono += seconds
        self._wall += seconds
