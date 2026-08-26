"""Fall features from keypoint history (Phase 4, Step 4.2).

Pure functions — no cv, no model. Consumes the COCO-17 skeletons that
``yolo26n-pose`` emits (via :meth:`TrackHistory.pose_history`) and derives
the four runbook features that feed the Step 4.3 rule cascade:

- ``torso_angle_deg``      shoulder-mid → hip-mid vector vs horizontal
                           (90 = upright, 0 = lying flat)
- ``head_hip_distance``    |head − hip-mid| normalized by bbox height
- ``hip_vertical_velocity`` px/s at the hip midpoint (+ = moving downward)
- ``bbox_aspect_ratio``    w / h (> 1 suggests a horizontal person)

CCTV reality (measured on bus1): hips occluded by seatbacks, back-of-head
views. A feature whose inputs are all below ``KEYPOINT_MIN_CONF`` is None —
the 4.3 rules must treat None as "unknown", never as "fine".
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from mobisentra.vision.pose import Keypoint, KeypointIndex
from mobisentra.vision.track_history import PoseSample

KEYPOINT_MIN_CONF: Final = 0.3

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class FallFeatures:
    torso_angle_deg: float | None
    head_hip_distance: float | None
    hip_vertical_velocity: float | None
    bbox_aspect_ratio: float | None


def _pair_midpoint(a: Keypoint, b: Keypoint) -> Point | None:
    """Both confident → midpoint; one confident → that point; else None."""
    if a[2] >= KEYPOINT_MIN_CONF and b[2] >= KEYPOINT_MIN_CONF:
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    best = a if a[2] >= b[2] else b
    return (best[0], best[1]) if best[2] >= KEYPOINT_MIN_CONF else None


def _head_point(sample: PoseSample) -> Point | None:
    """Best of nose/ears — CCTV faces are often turned away (nose ~0.1)."""
    candidates = (
        sample.keypoints[KeypointIndex.NOSE],
        sample.keypoints[KeypointIndex.LEFT_EAR],
        sample.keypoints[KeypointIndex.RIGHT_EAR],
    )
    best = max(candidates, key=lambda kp: kp[2])
    return (best[0], best[1]) if best[2] >= KEYPOINT_MIN_CONF else None


def _torso_angle(sample: PoseSample) -> float | None:
    shoulders = _pair_midpoint(
        sample.keypoints[KeypointIndex.LEFT_SHOULDER],
        sample.keypoints[KeypointIndex.RIGHT_SHOULDER],
    )
    hips = _pair_midpoint(
        sample.keypoints[KeypointIndex.LEFT_HIP], sample.keypoints[KeypointIndex.RIGHT_HIP]
    )
    if shoulders is None or hips is None:
        return None
    vx, vy = hips[0] - shoulders[0], hips[1] - shoulders[1]
    if vx == 0.0 and vy == 0.0:
        return None
    return math.degrees(math.atan2(abs(vy), abs(vx)))


def _hip_midpoints(samples: Sequence[PoseSample]) -> list[tuple[float, Point]]:
    return [
        (s.ts, mid)
        for s in samples
        if (
            mid := _pair_midpoint(
                s.keypoints[KeypointIndex.LEFT_HIP], s.keypoints[KeypointIndex.RIGHT_HIP]
            )
        )
        is not None
    ]


def _hip_velocity(samples: Sequence[PoseSample]) -> float | None:
    points = _hip_midpoints(samples)
    if len(points) < 2:
        return None
    (t_last, last), (t_prev, prev) = points[-1], points[-2]
    dt = t_last - t_prev
    if dt <= 0.0:
        return None
    return (last[1] - prev[1]) / dt


def compute_fall_features(samples: Sequence[PoseSample]) -> FallFeatures:
    """Derive fall features at the latest sample of a keypoint history."""
    if not samples:
        return FallFeatures(None, None, None, None)
    latest = samples[-1]

    angle = _torso_angle(latest)
    velocity = _hip_velocity(samples)

    head = _head_point(latest)
    hips = _pair_midpoint(
        latest.keypoints[KeypointIndex.LEFT_HIP], latest.keypoints[KeypointIndex.RIGHT_HIP]
    )
    if head is not None and hips is not None:
        x1, y1, x2, y2 = latest.bbox
        height = y2 - y1
        distance = (
            math.hypot(head[0] - hips[0], head[1] - hips[1]) / height if height > 0.0 else None
        )
    else:
        distance = None

    x1, y1, x2, y2 = latest.bbox
    aspect = (x2 - x1) / (y2 - y1) if y2 > y1 else None

    return FallFeatures(
        torso_angle_deg=angle,
        head_hip_distance=distance,
        hip_vertical_velocity=velocity,
        bbox_aspect_ratio=aspect,
    )
