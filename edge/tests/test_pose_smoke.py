"""Real pose-model smoke test (Phase 4, Step 4.1).

Same gating as tests/test_detection_smoke.py: skipped unless
MOBISENTRA_VISION_TEST=1 (CI stays light). Downloads yolo26n-pose.pt on
first run, runs PoseTracker over real bus1 frames, asserts tracked
skeletons with 17 COCO keypoints.

  MOBISENTRA_VISION_TEST=1 uv run pytest tests/test_pose_smoke.py -v -s
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MOBISENTRA_VISION_TEST") != "1",
        reason="set MOBISENTRA_VISION_TEST=1 (needs ultralytics + weights + real clip)",
    ),
]

CLIP = Path(__file__).resolve().parents[1] / "sample_data" / "videos" / "bus1.mp4"


@pytest.fixture(scope="module")
def frames():
    import cv2

    assert CLIP.is_file(), f"clip missing: {CLIP}"
    capture = cv2.VideoCapture(str(CLIP))
    grabbed = []
    for _ in range(60):
        ok, image = capture.read()
        if not ok:
            break
        grabbed.append(image)
    capture.release()
    assert len(grabbed) >= 40, "clip too short"
    return grabbed


@pytest.fixture(scope="module")
def tracker():
    pytest.importorskip("ultralytics")
    from mobisentra.vision.pose import PoseTracker

    return PoseTracker(
        model="yolo26n-pose.pt", conf=0.3, classes=[0], tracker="configs/tracktrack-tuned.yaml"
    )


def test_pose_tracker_yields_tracked_skeletons(tracker, frames):
    from mobisentra.vision.pose import N_KEYPOINTS

    poses_by_frame = [tracker.process_frame(frame) for frame in frames[::2]]
    non_empty = [poses for poses in poses_by_frame if poses]
    assert non_empty, "no poses detected in 30 frames of real footage"

    all_poses = [p for poses in non_empty for p in poses]
    assert all(len(p.keypoints) == N_KEYPOINTS for p in all_poses)
    assert all(p.track_id >= 0 for p in all_poses)
    assert all(0.0 <= kp[2] <= 1.0 for p in all_poses for kp in p.keypoints)

    ids_first = {p.track_id for p in non_empty[0]}
    ids_mid = {p.track_id for p in non_empty[len(non_empty) // 2]}
    assert ids_first & ids_mid, "track ids did not persist across frames"


def test_skeletons_are_anatomically_sane(tracker, frames):
    """On visible upper bodies, the head (best ear) sits above the shoulder line.

    bus1 reality: CCTV back-of-head views (nose/eye conf ~0.0–0.2) and
    seated passengers occluding hips behind seatbacks (hip conf ~0.0–0.4).
    Shoulders + ears are the reliable anatomy on this footage; hip-based
    checks are reserved for the UR Fall benchmark (Step 4.5).
    """
    from mobisentra.vision.pose import KeypointIndex

    checked = 0
    for frame in frames[::6]:
        for pose in tracker.process_frame(frame):
            ls = pose.keypoints[KeypointIndex.LEFT_SHOULDER]
            rs = pose.keypoints[KeypointIndex.RIGHT_SHOULDER]
            le = pose.keypoints[KeypointIndex.LEFT_EAR]
            re = pose.keypoints[KeypointIndex.RIGHT_EAR]
            ear, ear_conf = max(((le, le[2]), (re, re[2])), key=lambda pair: pair[1])
            if ear_conf > 0.5 and min(ls[2], rs[2]) > 0.5:
                shoulder_mid_y = (ls[1] + rs[1]) / 2
                assert ear[1] < shoulder_mid_y
                checked += 1
    assert checked >= 3, "not enough confident upper bodies to sanity-check"
