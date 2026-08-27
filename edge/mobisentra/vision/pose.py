"""Pose tracking on top of the Phase 2 detector wrapper (Phase 4, Step 4.1).

``yolo26n-pose`` detects persons AND emits 17 COCO keypoints in the same
``model.track(...)`` call — one pass, track IDs carried through. Public
surface mirrors :mod:`mobisentra.vision.tracker`: downstream fall analytics
consume :class:`TrackedPose`, never raw Results, so the model stays a
config value (``yolo11n-pose.pt`` = documented fallback).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from mobisentra.vision.tracker import DetectorTracker

N_KEYPOINTS = 17


class KeypointIndex(IntEnum):
    """COCO 17-keypoint ordering used by the YOLO pose family."""

    NOSE = 0
    LEFT_EYE = 1
    RIGHT_EYE = 2
    LEFT_EAR = 3
    RIGHT_EAR = 4
    LEFT_SHOULDER = 5
    RIGHT_SHOULDER = 6
    LEFT_ELBOW = 7
    RIGHT_ELBOW = 8
    LEFT_WRIST = 9
    RIGHT_WRIST = 10
    LEFT_HIP = 11
    RIGHT_HIP = 12
    LEFT_KNEE = 13
    RIGHT_KNEE = 14
    LEFT_ANKLE = 15
    RIGHT_ANKLE = 16


class PoseError(ValueError):
    """Raised when pose results don't match the expected COCO-17 layout."""


Keypoint = tuple[float, float, float]  # (x, y, confidence)


@dataclass(frozen=True)
class TrackedPose:
    track_id: int
    bbox: tuple[float, float, float, float]
    confidence: float
    keypoints: tuple[Keypoint, ...]


def postprocess_pose_results(result, tracked_classes: list[int] | None) -> list[TrackedPose]:
    """Convert one pose Results object to TrackedPose list.

    Pure function (mockable): boxes without track ids are dropped, classes
    outside ``tracked_classes`` are skipped, missing keypoints drop the
    person (a track without a skeleton is useless for fall analytics).
    """
    boxes = getattr(result, "boxes", None)
    keypoints = getattr(result, "keypoints", None)
    if boxes is None or boxes.id is None or keypoints is None or keypoints.data is None:
        return []

    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    ids = boxes.id.cpu().numpy().astype(int)
    clss = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else np.zeros_like(ids)
    kpts = keypoints.data.cpu().numpy()

    poses: list[TrackedPose] = []
    for index, ((x1, y1, x2, y2), conf, track_id, cls_id) in enumerate(
        zip(xyxy, confs, ids, clss, strict=True)
    ):
        if tracked_classes is not None and int(cls_id) not in tracked_classes:
            continue
        skeleton = kpts[index]
        if skeleton.shape != (N_KEYPOINTS, 3):
            raise PoseError(f"expected {N_KEYPOINTS} xyc keypoints, got shape {skeleton.shape}")
        poses.append(
            TrackedPose(
                track_id=int(track_id),
                bbox=(float(x1), float(y1), float(x2), float(y2)),
                confidence=float(conf),
                keypoints=tuple((float(x), float(y), float(c)) for x, y, c in skeleton),
            )
        )
    return poses


class PoseTracker(DetectorTracker):
    """DetectorTracker with pose postprocessing — same config, same tracker."""

    produces_pose: bool = True

    def _postprocess(self, result) -> list[TrackedPose]:
        return postprocess_pose_results(result, tracked_classes=self._classes)
