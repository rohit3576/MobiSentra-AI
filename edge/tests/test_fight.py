"""FightDetector fusion tests (Phase 5, Step 5.3) — synthetic sequences.

Core requirement: each signal alone is silent; only all four together fire.
Scenarios at 5 fps (dt=0.2): fight / hug / pass-by / rush / score-alone /
re-arm / forget. Boxes are 100x100; horizontal offset controls overlap,
alternating offsets control contact onsets, alternating jitter controls
relative motion (in box-diagonal units per second).
"""

from __future__ import annotations

from mobisentra.analytics.fight import EVENT_KIND, FightConfig, FightDetector
from mobisentra.analytics.pairs import Box, InteractionPair

DT = 0.2
FRAMES = 25


def boxes(dx: float, jitter_a: float, jitter_b: float, base_a: float = 0.0) -> dict[int, Box]:
    return {
        1: (base_a, 0.0, base_a + 100.0, 100.0),
        2: (base_a + dx, 0.0, base_a + dx + 100.0, 100.0),
    }


def run_scenario(
    detector: FightDetector,
    *,
    dx_seq: list[float],
    jitter: float,
    score: float,
) -> list:
    events = []
    pos_a = 0.0
    for i, dx in enumerate(dx_seq):
        ts = i * DT
        sign = 1.0 if i % 2 == 0 else -1.0
        pos_a += sign * jitter
        frame_boxes = boxes(dx, sign * jitter, -sign * jitter, base_a=pos_a)
        pair = InteractionPair(
            kind="interaction_pair",
            track_a=1,
            track_b=2,
            ts=ts,
            union_box=(0.0, 0.0, 300.0, 100.0),
            run_frames=99,
            first_proximate_ts=0.0,
        )
        events.extend(detector.update(ts, [pair], {(1, 2): score}, frame_boxes))
    return events


def contact_alternating(
    frames: int, contact_dx: float = 60.0, apart_dx: float = 130.0
) -> list[float]:
    seq = []
    for i in range(frames):
        phase = (i // 2) % 2
        seq.append(contact_dx if phase == 0 else apart_dx)
    return seq


def test_fight_all_four_signals_fires() -> None:
    detector = FightDetector(FightConfig())
    events = run_scenario(
        detector,
        dx_seq=contact_alternating(FRAMES),
        jitter=25.0,
        score=0.9,
    )
    assert len(events) == 1
    event = events[0]
    assert event.kind == EVENT_KIND
    assert (event.track_a, event.track_b) == (1, 2)
    assert event.ts - event.trigger_ts >= 0.6
    assert event.confidence > 0.5


def test_hug_high_score_low_motion_is_silent() -> None:
    detector = FightDetector(FightConfig())
    assert (
        run_scenario(detector, dx_seq=[50.0] * FRAMES, jitter=2.0, score=0.9) == []
    )


def test_pass_by_low_score_is_silent() -> None:
    detector = FightDetector(FightConfig())
    assert (
        run_scenario(detector, dx_seq=contact_alternating(FRAMES), jitter=25.0, score=0.2) == []
    )


def test_rush_no_contact_is_silent() -> None:
    detector = FightDetector(FightConfig())
    assert run_scenario(detector, dx_seq=[130.0] * FRAMES, jitter=25.0, score=0.9) == []


def test_grapple_sustained_contact_high_motion_fires() -> None:
    detector = FightDetector(FightConfig())
    events = run_scenario(detector, dx_seq=[45.0] * 20, jitter=25.0, score=0.85)
    assert len(events) == 1


def test_one_fire_per_engagement_then_rearm() -> None:
    detector = FightDetector(FightConfig())
    total = run_scenario(detector, dx_seq=contact_alternating(30), jitter=25.0, score=0.9)
    assert len(total) == 1
    cooldown = run_scenario(detector, dx_seq=contact_alternating(15), jitter=25.0, score=0.1)
    assert len(total + cooldown) == 1  # de-escalated score re-arms, no new fire while low
    total += run_scenario(
        detector,
        dx_seq=contact_alternating(20, contact_dx=55.0, apart_dx=135.0),
        jitter=25.0,
        score=0.9,
    )
    assert len(total) == 2  # fresh engagement fires again


def test_window_expires_old_contact() -> None:
    config = FightConfig(window_s=1.0, min_contact_onsets=2, sustained_contact_s=10.0)
    detector = FightDetector(config)
    seq = [60.0, 60.0] + [130.0] * 8  # contact only at the very start
    assert run_scenario(detector, dx_seq=seq, jitter=25.0, score=0.9) == []


def test_forget_drops_pair_state() -> None:
    detector = FightDetector(FightConfig())
    run_scenario(detector, dx_seq=contact_alternating(8), jitter=25.0, score=0.9)
    assert detector.pending_pair_ids() == [(1, 2)]
    detector.forget(1)
    assert detector.pending_pair_ids() == []


def test_pair_leaving_active_drops_state() -> None:
    detector = FightDetector(FightConfig())
    run_scenario(detector, dx_seq=contact_alternating(8), jitter=25.0, score=0.9)
    detector.update(99.0, [], {}, {1: (0, 0, 100, 100), 2: (500, 0, 600, 100)})
    assert detector.pending_pair_ids() == []
