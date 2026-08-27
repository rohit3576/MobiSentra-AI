"""Shared fixtures: synthetic video file + fakes."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest


@pytest.fixture()
def fake_clock():
    from fakes import FakeClock

    return FakeClock()


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """20-frame 64x48 MP4 whose frames encode their index as brightness."""
    path = tmp_path_factory.mktemp("media") / "synthetic.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (64, 48))
    for i in range(20):
        frame = np.full((48, 64, 3), 12 * (i + 1), dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path
