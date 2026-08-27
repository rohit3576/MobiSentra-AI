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
