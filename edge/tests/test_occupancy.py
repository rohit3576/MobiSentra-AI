"""Occupancy band + hysteresis tests (Phase 3, Step 3.2).

Synthetic count sequences only — no video, no cv. Bands per runbook:
Normal < 0.70 · Moderate 0.70–0.90 · Crowded > 0.90 · Overcrowded > 1.00.
A band change is confirmed only after N consecutive analyzed frames.
"""

import pytest

from mobisentra.analytics.occupancy import (
    OccupancyBand,
    OccupancyMonitor,
    band_for_ratio,
)


@pytest.mark.parametrize(
    ("count", "capacity", "expected"),
    [
        (0, 100, OccupancyBand.NORMAL),
        (69, 100, OccupancyBand.NORMAL),
        (70, 100, OccupancyBand.MODERATE),
        (90, 100, OccupancyBand.MODERATE),
        (91, 100, OccupancyBand.CROWDED),
        (100, 100, OccupancyBand.CROWDED),
        (101, 100, OccupancyBand.OVERCROWDED),
    ],
    ids=[
        "empty-normal",
        "just-below-70-normal",
        "at-70-moderate",
        "at-90-moderate",
        "above-90-crowded",
        "at-100-crowded",
        "above-100-overcrowded",
    ],
)
def test_band_boundaries(count, capacity, expected):
    assert band_for_ratio(count / capacity) is expected


def bands(mon: OccupancyMonitor, counts: list[int]) -> list[OccupancyBand]:
    return [mon.update(c).band for c in counts]


def monitor(capacity: int = 10, confirm: int = 3) -> OccupancyMonitor:
    return OccupancyMonitor(max_capacity=capacity, confirm_frames=confirm)


def test_first_observation_establishes_band_immediately():
    readings = bands(monitor(), [10, 10, 10])
    assert readings == [OccupancyBand.CROWDED] * 3


def test_band_change_confirmed_after_n_consecutive_frames():
    readings = bands(monitor(), [7, 10, 10, 10])
    assert readings == [
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.CROWDED,
    ]


def test_change_needs_full_streak():
    readings = bands(monitor(), [7, 10, 10])
    assert readings == [OccupancyBand.MODERATE] * 3


def test_no_flicker_at_band_boundary():
    # Counts wobbling 9/10 across the Moderate↔Crowded boundary never
    # accumulate 3 consecutive same-band observations → stays Moderate.
    readings = bands(monitor(), [9, 10, 9, 10, 9, 10, 9])
    assert readings == [OccupancyBand.MODERATE] * 7


def test_return_to_confirmed_band_resets_streak():
    readings = bands(monitor(), [7, 10, 10, 7, 10, 10, 10])
    assert readings == [
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.CROWDED,
    ]


def test_candidate_switch_resets_streak():
    # Crowded for 2 frames, then Overcrowded takes over as candidate — the
    # 3-frame clock restarts with the new candidate.
    readings = bands(monitor(), [7, 10, 11, 11, 11])
    assert readings == [
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.MODERATE,
        OccupancyBand.OVERCROWDED,
    ]


def test_confirm_frames_one_flips_immediately():
    readings = bands(monitor(confirm=1), [7, 10, 7])
    assert readings == [
        OccupancyBand.MODERATE,
        OccupancyBand.CROWDED,
        OccupancyBand.MODERATE,
    ]


def test_reading_carries_count_and_ratio():
    reading = monitor().update(7)
    assert reading.count == 7
    assert reading.ratio == pytest.approx(0.7)
    assert reading.band is OccupancyBand.MODERATE
