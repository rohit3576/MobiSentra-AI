"""Fall-feature tests on synthetic skeletons (Phase 4, Step 4.2).

Hand-placed COCO-17 coordinates — the truth is known by construction
(see test naming: standing / lying / bending). Runbook features: torso
angle, head–hip distance (normalized by bbox height), hip vertical
velocity, bbox aspect ratio. CCTV-occlusion cases: feature inputs below
KEYPOINT_MIN_CONF degrade to None (unknown), never fake a value.
"""

from __future__ import annotations

import pytest

from mobisentra.analytics.fall_features import KEYPOINT_MIN_CONF, compute_fall_features
from mobisentra.vision.pose import N_KEYPOINTS, Keypoint, KeypointIndex
from mobisentra.vision.track_history import PoseSample

CONF = 0.9


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


def pose_at(
    ts: float,
    bbox: tuple[float, float, float, float],
    keypoints: tuple[Keypoint, ...],
) -> PoseSample:
    return PoseSample(ts=ts, bbox=bbox, keypoints=keypoints)


STANDING_BBOX = (60.0, 25.0, 140.0, 175.0)  # 80 wide × 150 tall
LYING_BBOX = (25.0, 80.0, 175.0, 125.0)  # 150 wide × 45 thick
LYING = dict(
    nose=(30.0, 100.0),
    ls=(55.0, 95.0),
    rs=(55.0, 105.0),
    lh=(105.0, 95.0),
    rh=(105.0, 105.0),
)


def test_standing_skeleton():
    features = compute_fall_features([pose_at(1.0, STANDING_BBOX, skeleton())])
    assert features.torso_angle_deg == pytest.approx(90.0)
    assert features.head_hip_distance == pytest.approx(90.0 / 150.0)
    assert features.bbox_aspect_ratio == pytest.approx(80.0 / 150.0)


def test_lying_skeleton():
    features = compute_fall_features([pose_at(1.0, LYING_BBOX, skeleton(**LYING))])
    assert features.torso_angle_deg == pytest.approx(0.0, abs=1.0)
    assert features.head_hip_distance == pytest.approx(75.0 / 45.0)
    assert features.bbox_aspect_ratio == pytest.approx(150.0 / 45.0)


def test_bending_skeleton_is_intermediate():
    # shoulder mid (100,75) → hip mid (155,130): 55px right, 55px down = 45°
    bent = skeleton(lh=(151.0, 130.0), rh=(159.0, 130.0))
    features = compute_fall_features([pose_at(1.0, STANDING_BBOX, bent)])
    assert features.torso_angle_deg == pytest.approx(45.0, abs=1.0)


def test_hip_vertical_velocity_on_fall_sequence():
    samples = [
        pose_at(1.0, STANDING_BBOX, skeleton()),
        pose_at(1.4, (60.0, 85.0, 140.0, 235.0), skeleton(lh=(88.0, 190.0), rh=(112.0, 190.0))),
    ]
    features = compute_fall_features(samples)
    assert features.hip_vertical_velocity == pytest.approx(150.0)  # +60px / 0.4s, downward


def test_static_sequence_has_zero_velocity():
    samples = [pose_at(1.0, STANDING_BBOX, skeleton()), pose_at(1.3, STANDING_BBOX, skeleton())]
    features = compute_fall_features(samples)
    assert features.hip_vertical_velocity == pytest.approx(0.0)


def test_upward_motion_is_negative_velocity():
    samples = [
        pose_at(1.0, STANDING_BBOX, skeleton(lh=(88.0, 190.0), rh=(112.0, 190.0))),
        pose_at(1.5, STANDING_BBOX, skeleton()),
    ]
    features = compute_fall_features(samples)
    assert features.hip_vertical_velocity == pytest.approx(-120.0)


def test_velocity_none_without_history():
    features = compute_fall_features([pose_at(1.0, STANDING_BBOX, skeleton())])
    assert features.hip_vertical_velocity is None


def test_velocity_none_on_zero_dt():
    samples = [pose_at(1.0, STANDING_BBOX, skeleton()), pose_at(1.0, STANDING_BBOX, skeleton())]
    assert compute_fall_features(samples).hip_vertical_velocity is None


def test_occluded_hips_degrade_to_none():
    occluded = skeleton(lh=(0.0, 0.0, 0.05), rh=(0.0, 0.0, 0.1))
    features = compute_fall_features([pose_at(1.0, STANDING_BBOX, occluded)])
    assert features.torso_angle_deg is None
    assert features.head_hip_distance is None
    assert features.hip_vertical_velocity is None
    assert features.bbox_aspect_ratio is not None  # bbox survives


def test_single_visible_hip_still_computes():
    half = skeleton(rh=(0.0, 0.0, 0.05))
    features = compute_fall_features([pose_at(1.0, STANDING_BBOX, half)])
    assert features.torso_angle_deg is not None
    assert features.torso_angle_deg == pytest.approx(77.7, abs=0.5)


def test_missing_head_degrades_distance_only():
    faceless = skeleton()
    points = list(faceless)
    for index in (KeypointIndex.NOSE, KeypointIndex.LEFT_EAR, KeypointIndex.RIGHT_EAR):
        points[index] = (0.0, 0.0, 0.05)
    features = compute_fall_features([pose_at(1.0, STANDING_BBOX, tuple(points))])
    assert features.head_hip_distance is None
    assert features.torso_angle_deg == pytest.approx(90.0)


def test_min_conf_constant_is_sane():
    assert 0.0 < KEYPOINT_MIN_CONF < 0.9


# ── head_hip_vertical_offset (Step 4.3 rule input) ─────────────────────────

from mobisentra.analytics.fall_features import head_hip_vertical_offset  # noqa: E402


def test_head_hip_vertical_offset_standing():
    offset = head_hip_vertical_offset(pose_at(1.0, STANDING_BBOX, skeleton()))
    assert offset == pytest.approx(90.0 / 150.0)


def test_head_hip_vertical_offset_lying_is_near_zero():
    offset = head_hip_vertical_offset(pose_at(1.0, LYING_BBOX, skeleton(**LYING)))
    assert offset is not None
    assert offset < 0.15


def test_head_hip_vertical_offset_occluded_is_none():
    occluded = skeleton(lh=(0.0, 0.0, 0.05), rh=(0.0, 0.0, 0.1),
                        nose=(0.0, 0.0, 0.05))
    assert head_hip_vertical_offset(pose_at(1.0, STANDING_BBOX, occluded)) is None


def test_head_hip_vertical_offset_degenerate_bbox_is_none():
    assert head_hip_vertical_offset(pose_at(1.0, (0.0, 50.0, 80.0, 50.0), skeleton())) is None
