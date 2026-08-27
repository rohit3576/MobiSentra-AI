"""Rule-cascade tests on synthetic sequences (Phase 4, Step 4.3).

The runbook trio — fall vs sit vs bend — plus recovery, re-arm, occlusion
and configurability. Geometry: standing person occupies y 25..175 (hips at
130); a collapse ends ON THE GROUND PLANE (lying torso centered y≈170,
bbox 150×45) — hip drop 40 px over 0.4 s = 1.03 body-heights/s.
"""

from __future__ import annotations

from mobisentra.analytics.fall import FallConfig, FallDetector
from mobisentra.vision.pose import N_KEYPOINTS, Keypoint, KeypointIndex
from mobisentra.vision.track_history import PoseSample

CONF = 0.9
STANDING_BBOX = (60.0, 25.0, 140.0, 175.0)
GROUND_BBOX = (20.0, 150.0, 180.0, 195.0)


def skeleton(
    *,
    nose: tuple[float, float] = (100.0, 40.0),
    ls: tuple[float, float] = (85.0, 75.0),
    rs: tuple[float, float] = (115.0, 75.0),
    lh: tuple[float, float] = (88.0, 130.0),
    rh: tuple[float, float] = (112.0, 130.0),
    conf: float = CONF,
) -> tuple[Keypoint, ...]:
    points: list[Keypoint] = [(50.0, 50.0, conf)] * N_KEYPOINTS
    points[KeypointIndex.NOSE] = (*nose, conf)
    points[KeypointIndex.LEFT_SHOULDER] = (*ls, conf)
    points[KeypointIndex.RIGHT_SHOULDER] = (*rs, conf)
    points[KeypointIndex.LEFT_HIP] = (*lh, conf)
    points[KeypointIndex.RIGHT_HIP] = (*rh, conf)
    points[KeypointIndex.LEFT_EAR] = (nose[0] - 6, nose[1] + 4, conf)
    points[KeypointIndex.RIGHT_EAR] = (nose[0] + 6, nose[1] + 4, conf)
    return tuple(points)


STANDING = skeleton()
FALLEN = skeleton(
    nose=(30.0, 172.0),
    ls=(55.0, 162.0),
    rs=(55.0, 182.0),
    lh=(110.0, 162.0),
    rh=(110.0, 182.0),
)
SEATED = skeleton(ls=(85.0, 120.0), rs=(115.0, 120.0), lh=(85.0, 160.0), rh=(115.0, 160.0))
BENT = skeleton(ls=(130.0, 120.0), rs=(160.0, 120.0))


def at(ts: float, bbox=STANDING_BBOX, keypoints=STANDING) -> PoseSample:
    return PoseSample(ts=ts, bbox=bbox, keypoints=keypoints)


def feed(detector: FallDetector, samples: list[PoseSample]):
    events = []
    for index in range(1, len(samples) + 1):
        events.extend(detector.update(track_id=1, samples=samples[:index]))
    return events


def test_track_switch_during_confirm_window_still_fires():
    """UR Fall fall-01 (2026-08-27): trackers re-label the person mid-collapse
    (id 1 triggers at t=3.5, lying body becomes id 2). The confirm window runs
    on the caller's frame clock, so a trigger track frozen in the lying pose
    still fires; the successor lying in the area is consistent, not recovery."""
    detector = FallDetector()
    trigger_track = [at(0.0), at(0.4, GROUND_BBOX, FALLEN)]
    successor = [
        at(0.8, GROUND_BBOX, FALLEN),
        at(2.0, GROUND_BBOX, FALLEN),
        at(4.0, GROUND_BBOX, FALLEN),
    ]

    events = []
    events.extend(detector.update(track_id=1, samples=trigger_track, now_ts=0.4))
    for index in range(1, len(successor) + 1):
        events.extend(
            detector.update(
                track_id=1,
                samples=trigger_track,
                now_ts=successor[index - 1].ts,
                others={2: successor[:index]},
            )
        )
    assert len(events) == 1
    assert events[0].trigger_ts == 0.4
    assert events[0].ts == 4.0


def test_frozen_trigger_track_confirms_on_frame_clock_alone():
    detector = FallDetector()
    frozen = [at(0.0), at(0.4, GROUND_BBOX, FALLEN)]
    assert detector.update(track_id=1, samples=frozen, now_ts=0.4) == []
    events = detector.update(track_id=1, samples=frozen, now_ts=4.0)
    assert [event.ts for event in events] == [4.0]


def test_new_id_getting_up_in_trigger_area_is_recovery_not_fall():
    detector = FallDetector()
    trigger_track = [at(0.0), at(0.4, GROUND_BBOX, FALLEN)]
    successor = [
        at(1.0, GROUND_BBOX, FALLEN),
        at(2.0, STANDING_BBOX, STANDING),
        at(4.0, STANDING_BBOX, STANDING),
    ]

    events = []
    events.extend(detector.update(track_id=1, samples=trigger_track, now_ts=0.4))
    for index in range(1, len(successor) + 1):
        events.extend(
            detector.update(
                track_id=1,
                samples=trigger_track,
                now_ts=successor[index - 1].ts,
                others={2: successor[:index]},
            )
        )
    assert events == []


def test_frozen_history_cannot_retrigger_after_recovery():
    """Real-run bug (fall-01, 2026-08-27): a recovered track whose samples
    froze mid-collapse re-armed on the OLD velocity evidence and re-fired.
    Triggers must evaluate fresh observations only."""
    detector = FallDetector()
    frozen = [at(0.0), at(0.4, GROUND_BBOX, FALLEN)]
    successor_upright = [at(1.0, STANDING_BBOX, STANDING), at(2.0, STANDING_BBOX, STANDING)]

    events = []
    events.extend(detector.update(track_id=1, samples=frozen, now_ts=0.4))
    for index in range(1, len(successor_upright) + 1):
        events.extend(
            detector.update(
                track_id=1,
                samples=frozen,
                now_ts=successor_upright[index - 1].ts,
                others={2: successor_upright[:index]},
            )
        )
    for now in (3.0, 4.0, 6.0, 8.0):
        events.extend(
            detector.update(track_id=1, samples=frozen, now_ts=now, others={2: successor_upright})
        )
    assert events == []


def test_lying_pose_jitter_does_not_trigger():
    """UR Fall adl-30 (2026-08-27): hip-velocity on an already-lying body
    jitters (pose flip-flop between lying variants ≈ ±5 BH/s) with a
    horizontal torso — velocity + angle alone false-triggered. A real fall
    collapses from UPRIGHT within a second; a jittering body has been down
    for a while."""
    detector = FallDetector()
    lying_bbox = (60.0, 150.0, 140.0, 195.0)
    lying_low = FALLEN
    lying_high = skeleton(
        nose=(30.0, 142.0), ls=(55.0, 132.0), rs=(55.0, 152.0), lh=(110.0, 132.0), rh=(110.0, 152.0)
    )
    samples = [
        at(0.0, lying_bbox, lying_low),
        at(0.2, lying_bbox, lying_high),
        at(0.4, lying_bbox, lying_low),
        at(0.6, lying_bbox, lying_high),
        at(0.8, lying_bbox, lying_low),
        at(1.0, lying_bbox, lying_high),
        at(1.2, lying_bbox, lying_low),
    ]
    for index in range(2, len(samples) + 1):
        assert detector.update(track_id=1, samples=samples[:index]) == []


def test_fall_from_upright_within_window_triggers_despite_small_height_change():
    """UR Fall fall-23 (2026-08-27): a far-field fall whose bbox height
    barely collapses still has an upright sample <1 s before the trigger —
    the upright-recency gate must let it through (a bbox-height gate
    rejected it)."""
    detector = FallDetector()
    shallow_bbox = (60.0, 80.0, 140.0, 185.0)
    ground_shallow = (20.0, 105.0, 180.0, 190.0)
    samples = [
        at(1.0, shallow_bbox, STANDING),
        at(1.3, shallow_bbox, STANDING),
        at(1.6, ground_shallow, FALLEN),
        at(1.9, ground_shallow, FALLEN),
        at(4.9, ground_shallow, FALLEN),
    ]
    events = [
        event
        for index in range(2, len(samples) + 1)
        for event in detector.update(track_id=1, samples=samples[:index])
    ]
    assert [event.ts for event in events] == [4.9]


def test_preexisting_standing_duplicate_does_not_recover_a_fall():
    """UR Fall fall-23 (2026-08-27): a duplicate upright track born BEFORE
    the trigger lives past trigger + sustain and used to satisfy the
    other-track recovery gate — the real fall never confirmed. Recovery via
    others requires a track BORN after the trigger (a successor)."""
    detector = FallDetector()
    falling = [
        at(0.0),
        at(1.3, STANDING_BBOX, STANDING),
        at(1.6, GROUND_BBOX, FALLEN),
    ]
    duplicate = [
        at(0.8, STANDING_BBOX, STANDING),
        at(1.8, STANDING_BBOX, STANDING),
        at(2.4, STANDING_BBOX, STANDING),
        at(4.0, STANDING_BBOX, STANDING),
        at(5.0, STANDING_BBOX, STANDING),
    ]

    events = []
    for index in range(1, len(duplicate) + 1):
        events.extend(
            detector.update(
                track_id=1,
                samples=falling if index > 1 else falling[:2],
                now_ts=duplicate[index - 1].ts,
                others={7: duplicate[:index]},
            )
        )
    assert [event.ts for event in events] == [5.0]


def test_knee_drop_fall_triggers_on_near_free_fall_velocity():
    """UR Fall fall-25/27/29 (2026-08-27): knee-drop falls keep the torso at
    60–80° the whole way down — the torso gate never fires — but the drop
    runs at 2.5+ BH/s, far above any fast sit (≈1.0–1.5)."""
    detector = FallDetector()
    drop1_bbox = (35.0, 50.0, 155.0, 200.0)
    drop2_bbox = (30.0, 70.0, 160.0, 205.0)
    kneel_bbox = (30.0, 80.0, 160.0, 210.0)
    drop1 = skeleton(ls=(80.0, 100.0), rs=(110.0, 100.0), lh=(85.0, 163.0), rh=(105.0, 163.0))
    drop2 = skeleton(ls=(80.0, 103.0), rs=(110.0, 103.0), lh=(85.0, 196.0), rh=(105.0, 196.0))
    kneeling = skeleton(
        ls=(80.0, 105.0),
        rs=(110.0, 105.0),
        lh=(85.0, 196.0),
        rh=(105.0, 196.0),
    )
    events = feed(
        detector,
        [
            at(1.90, STANDING_BBOX, STANDING),
            at(1.97, drop1_bbox, drop1),
            at(2.04, drop2_bbox, drop2),
            at(2.2, kneel_bbox, kneeling),
            at(5.2, kneel_bbox, kneeling),
        ],
    )
    assert [event.ts for event in events] == [5.2]


def test_fast_sit_stays_under_the_high_bar():
    """A fast sit: torso stays upright, hips drop ~0.35 body-heights in
    ~0.4 s ≈ 0.9 BH/s — must not fire on either path."""
    detector = FallDetector()
    seated_bbox = (60.0, 55.0, 140.0, 205.0)
    events = feed(
        detector,
        [
            at(1.0, STANDING_BBOX, STANDING),
            at(1.4, seated_bbox, SEATED),
            at(1.8, seated_bbox, SEATED),
            at(4.8, seated_bbox, SEATED),
        ],
    )
    assert events == []


def test_full_fall_fires_exactly_once_after_confirm_window():
    detector = FallDetector()
    events = feed(
        detector,
        [
            at(0.0),
            at(0.4, GROUND_BBOX, FALLEN),
            at(0.8, GROUND_BBOX, FALLEN),
            at(1.6, GROUND_BBOX, FALLEN),
            at(2.4, GROUND_BBOX, FALLEN),
            at(3.2, GROUND_BBOX, FALLEN),
            at(4.0, GROUND_BBOX, FALLEN),
            at(4.8, GROUND_BBOX, FALLEN),
        ],
    )

    assert len(events) == 1
    event = events[0]
    assert event.kind == "fall_detected"
    assert event.track_id == 1
    assert event.trigger_ts == 0.4
    assert event.ts == 4.0
    assert 0.5 <= event.confidence <= 1.0


def test_slow_sit_never_triggers():
    detector = FallDetector()
    events = feed(
        detector,
        [
            at(0.0),
            at(0.5, (60.0, 55.0, 140.0, 205.0), skeleton(lh=(88.0, 145.0), rh=(112.0, 145.0))),
            at(1.0, (60.0, 55.0, 140.0, 205.0), SEATED),
            at(2.0, (60.0, 55.0, 140.0, 205.0), SEATED),
            at(4.5, (60.0, 55.0, 140.0, 205.0), SEATED),
        ],
    )
    assert events == []


def test_fast_bend_never_triggers():
    detector = FallDetector()
    events = feed(
        detector,
        [
            at(0.0),
            at(0.3, STANDING_BBOX, BENT),
            at(0.6, STANDING_BBOX, BENT),
            at(3.6, STANDING_BBOX, BENT),
        ],
    )
    assert events == []


def test_stumble_with_quick_recovery_never_fires():
    detector = FallDetector()
    events = feed(
        detector,
        [
            at(0.0),
            at(0.4, GROUND_BBOX, FALLEN),
            at(1.2, STANDING_BBOX, STANDING),
            at(2.0, STANDING_BBOX, STANDING),
            at(4.0, STANDING_BBOX, STANDING),
        ],
    )
    assert events == []


def test_second_fall_after_recovery_fires_again():
    detector = FallDetector()
    events = feed(
        detector,
        [
            at(0.0),
            at(0.4, GROUND_BBOX, FALLEN),
            at(1.0, GROUND_BBOX, FALLEN),
            at(2.0, GROUND_BBOX, FALLEN),
            at(4.0, GROUND_BBOX, FALLEN),
            at(5.0, STANDING_BBOX, STANDING),
            at(5.4, GROUND_BBOX, FALLEN),
            at(6.4, GROUND_BBOX, FALLEN),
            at(7.4, GROUND_BBOX, FALLEN),
            at(8.8, GROUND_BBOX, FALLEN),
        ],
    )
    assert [event.ts for event in events] == [4.0, 8.8]


def test_occluded_confirm_window_still_fires():
    detector = FallDetector()
    occluded = skeleton(
        nose=(0.0, 0.0, 0.05),
        ls=(0.0, 0.0, 0.05),
        rs=(0.0, 0.0, 0.05),
        lh=(0.0, 0.0, 0.05),
        rh=(0.0, 0.0, 0.05),
    )
    events = feed(
        detector,
        [
            at(0.0),
            at(0.4, GROUND_BBOX, FALLEN),
            at(1.0, GROUND_BBOX, occluded),
            at(2.0, GROUND_BBOX, occluded),
            at(4.0, GROUND_BBOX, occluded),
        ],
    )
    assert len(events) == 1


def test_confirm_window_is_configurable():
    detector = FallDetector(FallConfig(confirm_seconds=1.0))
    events = feed(
        detector,
        [
            at(0.0),
            at(0.4, GROUND_BBOX, FALLEN),
            at(1.6, GROUND_BBOX, FALLEN),
        ],
    )
    assert [event.ts for event in events] == [1.6]


def test_forget_clears_pending_state():
    detector = FallDetector()
    detector.update(track_id=1, samples=[at(0.0), at(0.4, GROUND_BBOX, FALLEN)])
    detector.forget(track_id=1)
    events = detector.update(
        track_id=1,
        samples=[at(0.0), at(0.4, GROUND_BBOX, FALLEN), at(4.0, GROUND_BBOX, FALLEN)],
    )
    assert events == []


def test_empty_and_single_sample_history_are_safe():
    detector = FallDetector()
    assert detector.update(track_id=1, samples=[]) == []
    assert detector.update(track_id=1, samples=[at(0.0)]) == []


def test_confidence_rewards_strong_signals():
    fast = FallDetector()
    gradual = FallDetector()
    fast_events = feed(
        fast,
        [
            at(0.0),
            at(0.2, GROUND_BBOX, FALLEN),
            at(1.0, GROUND_BBOX, FALLEN),
            at(2.0, GROUND_BBOX, FALLEN),
            at(3.4, GROUND_BBOX, FALLEN),
            at(4.0, GROUND_BBOX, FALLEN),
        ],
    )
    gradual_events = feed(
        gradual,
        [
            at(0.0),
            at(0.4, GROUND_BBOX, FALLEN),
            at(1.0, GROUND_BBOX, FALLEN),
            at(2.0, GROUND_BBOX, FALLEN),
            at(3.4, GROUND_BBOX, FALLEN),
            at(4.0, GROUND_BBOX, FALLEN),
        ],
    )
    assert fast_events[0].confidence > gradual_events[0].confidence
