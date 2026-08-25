"""Frame model passed from reader threads to consumers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    capture_ts: float
    frame_index: int
    source_id: str
