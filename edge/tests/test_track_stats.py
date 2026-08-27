"""track_stats pure-function tests (no model, no video)."""

from __future__ import annotations

from tools.track_stats import (
    Sample,
    compute_fragmentation,
    count_reassociations,
    iou,
)


def sample(track: list[tuple[int, tuple]], tid: int) -> list[Sample]:
    return [Sample(frame=f, bbox=b) for f, b in track]


BOX = (0, 0, 10, 10)


def test_iou_identical_and_disjoint():
    assert iou(BOX, BOX) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert abs(iou((0, 0, 10, 10), (5, 0, 15, 10)) - 50 / 150) < 1e-9


def test_fragmentation_ratios():
    tracks = {
        1: sample([(i, BOX) for i in range(300)], 1),  # 10s @30fps
        2: sample([(i, BOX) for i in range(90)], 2),  # 3s
        3: sample([(i, BOX) for i in range(15)], 3),  # 0.5s flicker
    }
    report = compute_fragmentation(tracks, fps=30.0, min_lifetime_s=10.0)
    assert report.n_tracks == 3
    assert report.n_stable_tracks == 1
    assert abs(report.stable_ratio - 300 / 405) < 1e-9
    assert abs(report.stable_ratio_flicker_filtered - 300 / 390) < 1e-9


def test_reassociation_detected():
    tracks = {
        1: sample([(i, BOX) for i in range(100)], 1),
        2: sample([(i, BOX) for i in range(110, 200)], 2),  # same place, new ID
    }
    assert count_reassociations(tracks) == 1


def test_walkthrough_not_counted():
    tracks = {
        1: sample([(i, BOX) for i in range(100)], 1),
        2: sample([(i, (100, 100, 110, 110)) for i in range(110, 200)], 2),
    }
    assert count_reassociations(tracks) == 0


def test_late_start_not_counted():
    tracks = {
        1: sample([(i, BOX) for i in range(10)], 1),
        2: sample([(i, BOX) for i in range(100, 120)], 2),  # 3s later, same place
    }
    assert count_reassociations(tracks) == 0
