"""Occupancy monitoring (Phase 3, Step 3.2).

Pure logic — no cv, no I/O. Zone head-count → capacity ratio → band
(Normal < 0.70 · Moderate 0.70–0.90 · Crowded > 0.90 · Overcrowded > 1.00,
runbook values). Band changes are confirmed only after ``confirm_frames``
consecutive analyzed frames — tracker counts wobble ±1 around thresholds
and unfiltered flips would flicker events.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

MODERATE_THRESHOLD: Final = 0.70
CROWDED_THRESHOLD: Final = 0.90
OVERCROWDED_THRESHOLD: Final = 1.00


class OccupancyBand(StrEnum):
    NORMAL = "normal"
    MODERATE = "moderate"
    CROWDED = "crowded"
    OVERCROWDED = "overcrowded"


def band_for_ratio(ratio: float) -> OccupancyBand:
    """Map a capacity ratio to its occupancy band (range checks, not variants)."""
    if ratio > OVERCROWDED_THRESHOLD:
        return OccupancyBand.OVERCROWDED
    if ratio > CROWDED_THRESHOLD:
        return OccupancyBand.CROWDED
    if ratio >= MODERATE_THRESHOLD:
        return OccupancyBand.MODERATE
    return OccupancyBand.NORMAL


@dataclass(frozen=True, slots=True)
class OccupancyReading:
    band: OccupancyBand
    count: int
    ratio: float


class OccupancyMonitor:
    """Per-occupancy-zone band state machine.

    Mutable by design: it exists to accumulate a confirmation streak across
    frames. ``update`` is called once per analyzed frame (framerate-agnostic —
    ``analyze_every_n_frames`` throttling happens upstream).
    """

    def __init__(self, *, max_capacity: int, confirm_frames: int = 30) -> None:
        self._max_capacity = max_capacity
        self._confirm_frames = confirm_frames
        self._confirmed: OccupancyBand | None = None
        self._candidate: OccupancyBand | None = None
        self._streak = 0

    def update(self, count: int) -> OccupancyReading:
        """Feed this analyzed frame's in-zone head count; returns confirmed state."""
        observed = band_for_ratio(count / self._max_capacity)

        if self._confirmed is None:
            self._confirmed = observed
        elif observed is self._confirmed:
            self._streak = 0
            self._candidate = None
        else:
            if observed is not self._candidate:
                self._candidate = observed
                self._streak = 0
            self._streak += 1
            if self._streak >= self._confirm_frames:
                self._confirmed = observed
                self._candidate = None
                self._streak = 0

        return OccupancyReading(band=self._confirmed, count=count, ratio=count / self._max_capacity)
