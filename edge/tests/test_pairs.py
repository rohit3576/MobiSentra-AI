"""PairFinder unit tests (Phase 5, Step 5.2) — synthetic boxes, pure logic."""

from __future__ import annotations

from mobisentra.analytics.pairs import EVENT_KIND, Box, PairConfig, PairFinder, iou

A: Box = (0.0, 0.0, 100.0, 100.0)
B: Box = (50.0, 0.0, 150.0, 100.0)  # IoU(A,B) = 0.333
NEAR: Box = (110.0, 0.0, 210.0, 100.0)  # IoU 0, center dist 0.78 diags
FAR: Box = (300.0, 0.0, 400.0, 100.0)  # IoU 0, center dist 1.77 diags


def run_frames(finder: PairFinder, boxes: dict[int, Box], frames: int, t0: float = 0.0):
    out = []
    for i in range(frames):
        out.extend(finder.update(t0 + i, boxes))
    return out


def test_iou_known_value() -> None:
    assert iou(A, B) == 0.0 or abs(iou(A, B) - 1 / 3) < 1e-9


def test_sustained_overlap_fires_exactly_at_threshold() -> None:
    config = PairConfig(sustain_frames=5, gap_frames=2)
    finder = PairFinder(config)
    events = run_frames(finder, {1: A, 2: B}, frames=4)
    assert events == []
    events = run_frames(finder, {1: A, 2: B}, frames=1, t0=4.0)
    assert len(events) == 1
    pair = events[0]
    assert pair.kind == EVENT_KIND
    assert (pair.track_a, pair.track_b) == (1, 2)
    assert pair.run_frames == 5
    assert pair.first_proximate_ts == 0.0


def test_single_frame_overlap_never_fires() -> None:
    finder = PairFinder(PairConfig(sustain_frames=5, gap_frames=2))
    assert finder.update(0.0, {1: A, 2: B}) == []  # run 1
    assert finder.update(1.0, {1: A, 2: FAR}) == []  # miss 1
    assert finder.update(2.0, {1: A, 2: FAR}) == []  # miss 2 — state alive through the gap
    assert finder.update(3.0, {1: A, 2: B}) == []  # run 2 — continuity proves survival
    assert finder.active_pair_ids() == []  # never reaches sustain
    for i in range(4, 9):
        assert finder.update(float(i), {1: A, 2: FAR}) == []  # misses > gap → dropped
    assert finder.active_pair_ids() == []
    assert finder.update(9.0, {1: A, 2: B}) == []  # fresh run at 1 — no memory of the old one


def test_distant_boxes_never_pair() -> None:
    finder = PairFinder()
    for i in range(20):
        assert finder.update(float(i), {1: A, 2: FAR}) == []
    assert finder.active_pair_ids() == []


def test_near_without_overlap_fires_via_center_distance() -> None:
    finder = PairFinder(PairConfig(sustain_frames=3))
    events = run_frames(finder, {1: A, 2: NEAR}, frames=3)
    assert len(events) == 1
    assert events[0].track_b == 2


def test_tolerated_gap_keeps_run_and_pair_alive() -> None:
    finder = PairFinder(PairConfig(sustain_frames=3, gap_frames=2))
    assert finder.update(0.0, {1: A, 2: B}) == []
    assert finder.update(1.0, {1: A, 2: B}) == []
    assert finder.update(2.0, {1: A, 2: FAR}) == []  # miss 1 — tolerated
    events = finder.update(3.0, {1: A, 2: B})  # run resumes at 3 → fires
    assert len(events) == 1
    assert events[0].run_frames == 3


def test_gap_over_limit_resets_run() -> None:
    finder = PairFinder(PairConfig(sustain_frames=3, gap_frames=1))
    finder.update(0.0, {1: A, 2: B})
    finder.update(1.0, {1: A, 2: B})
    finder.update(2.0, {1: A, 2: FAR})  # miss 1 — tolerated
    assert finder.update(3.0, {1: A, 2: FAR}) == []  # miss 2 > gap → dropped
    assert finder.update(4.0, {1: A, 2: B}) == []  # fresh run at 1
    events = run_frames(finder, {1: A, 2: B}, frames=2, t0=5.0)
    assert len(events) == 1
    assert events[0].first_proximate_ts == 4.0


def test_union_box_is_crop_source() -> None:
    finder = PairFinder(PairConfig(sustain_frames=1))
    events = finder.update(0.0, {1: A, 2: B})
    assert len(events) == 1
    assert events[0].union_box == (0.0, 0.0, 150.0, 100.0)


def test_emission_every_frame_once_active_with_current_union() -> None:
    finder = PairFinder(PairConfig(sustain_frames=2))
    assert finder.update(0.0, {1: A, 2: B}) == []
    assert finder.update(1.0, {1: A, 2: B}) != []
    moved = {1: A, 2: (60.0, 10.0, 160.0, 110.0)}
    events = finder.update(2.0, moved)
    assert len(events) == 1
    assert events[0].union_box == (0.0, 0.0, 160.0, 110.0)
    assert events[0].run_frames == 3


def test_forget_drops_pairs_containing_track() -> None:
    finder = PairFinder(PairConfig(sustain_frames=2))
    finder.update(0.0, {1: A, 2: B, 3: NEAR})
    finder.forget(2)
    assert finder.active_pair_ids() == []
    assert finder.update(1.0, {1: A, 2: B}) == []  # run restarted from scratch


def test_both_tracks_absent_drops_pair() -> None:
    finder = PairFinder(PairConfig(sustain_frames=2))
    finder.update(0.0, {1: A, 2: B})
    finder.update(1.0, {})  # nobody in frame → pair state dropped, not gapped
    events = run_frames(finder, {1: A, 2: B}, frames=2, t0=2.0)
    assert events != []
    assert events[-1].first_proximate_ts == 2.0


def test_degenerate_box_is_never_proximate() -> None:
    finder = PairFinder(PairConfig(sustain_frames=1))
    assert finder.update(0.0, {1: (10.0, 10.0, 10.0, 10.0), 2: (10.0, 10.0, 11.0, 11.0)}) == []
