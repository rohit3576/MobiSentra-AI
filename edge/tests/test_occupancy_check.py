"""Occupancy-check pure-function tests (Phase 3, Step 3.6 — Gate 3 evidence).

``sample_frames`` picks deterministic spread indices; ``verdict_rows``
applies the gate rule |measured − manual| ≤ max(1, 0.10 × manual).
"""

from __future__ import annotations

import pytest

from tools.occupancy_check import sample_frames, verdict_rows


def test_sample_frames_spreads_across_clip():
    assert sample_frames(total=100, k=5) == [10, 30, 50, 69, 89]


def test_sample_frames_are_integers_for_odd_totals():
    frames = sample_frames(total=57, k=5)
    assert len(frames) == 5
    assert frames == sorted(frames)
    assert frames[0] > 0 and frames[-1] < 57
    assert all(isinstance(f, int) for f in frames)


def test_sample_frames_more_samples_than_frames():
    assert sample_frames(total=3, k=5) == [1]


def test_sample_frames_single_frame_clip():
    assert sample_frames(total=1, k=5) == [0]


def test_sample_frames_rejects_empty_clip():
    with pytest.raises(ValueError, match="no frames"):
        sample_frames(total=0, k=5)


@pytest.mark.parametrize(
    ("measured", "manual", "passes"),
    [
        (14, 15, True),  # delta 1 ≤ tol 1.5
        (12, 15, False),  # delta 3 > tol 1.5
        (8, 7, True),  # under 10 → tol is exactly 1
        (9, 7, False),  # delta 2 > 1
        (5, 5, True),  # exact
    ],
    ids=["within-10pct", "outside-10pct", "under10-within-1", "under10-off-by-2", "exact"],
)
def test_verdict_boundaries(measured, manual, passes):
    rows = verdict_rows(
        samples=[{"frame_index": 10, "ts": 1.0, "measured": measured}],
        manual={10: manual},
    )
    assert rows[0].passes is passes
    assert rows[0].tolerance == max(1, round(0.10 * manual, 10))


def test_verdict_requires_manual_for_every_sample():
    with pytest.raises(KeyError):
        verdict_rows(
            samples=[{"frame_index": 10, "ts": 1.0, "measured": 5}],
            manual={99: 5},
        )


def test_verdict_all_pass_flag():
    rows = verdict_rows(
        samples=[
            {"frame_index": 10, "ts": 1.0, "measured": 5},
            {"frame_index": 20, "ts": 2.0, "measured": 20},
        ],
        manual={10: 5, 20: 18},
    )
    assert [row.passes for row in rows] == [True, False]
