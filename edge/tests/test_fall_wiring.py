"""Fall + evidence wiring tests (Phase 4, Step 4.4).

End-to-end minus the neural net: synthetic pose streams through
``TrackHistory`` → ``CameraAnalytics.process`` → ``fall_detected`` row whose
``evidence_ref`` points at a playable clip on disk (reopened with cv2), and
the ``run_frame`` pose branch feeding keypoint history from a PoseTracker.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from test_fall import FALLEN, GROUND_BBOX, STANDING, STANDING_BBOX, at

from mobisentra.analytics.engine import CameraAnalytics
from mobisentra.ingestion.config import CameraConfig, ZoneConfig, ZoneType
from mobisentra.pipeline import CameraAccumulator, run_frame
from mobisentra.vision.pose import TrackedPose
from mobisentra.vision.track_history import TrackHistory

FRAME = np.full((200, 300, 3), 40, dtype=np.uint8)
REST_ZONE_AROUND_PERSON = ((0.2, 0.6), (0.55, 0.6), (0.55, 1.0), (0.2, 1.0))
REST_ZONE_ELSEWHERE = ((0.8, 0.0), (1.0, 0.0), (1.0, 0.2), (0.8, 0.2))


def make_camera(zones: dict | None = None) -> CameraConfig:
    return CameraConfig(
        id="CAM_FALL", source="sample://videos/f.mp4", vehicle_id="V", zones=zones or {}
    )


def sample_to_pose(sample) -> TrackedPose:
    return TrackedPose(track_id=1, bbox=sample.bbox, confidence=0.9, keypoints=sample.keypoints)


def feed_through_engine(analytics: CameraAnalytics, history: TrackHistory, script) -> list[dict]:
    from mobisentra.vision.tracker import TrackedPerson

    rows: list[dict] = []
    for ts, sample in script:
        pose = sample_to_pose(sample)
        person = TrackedPerson(track_id=pose.track_id, bbox=pose.bbox, confidence=pose.confidence)
        history.update(ts, [person])
        history.update_poses(ts, [pose])
        rows.extend(analytics.process(ts, FRAME, [person]))
    return rows


FALL_SCRIPT = [
    (0.0, at(0.0)),
    (0.4, at(0.4, GROUND_BBOX, FALLEN)),
    (1.0, at(1.0, GROUND_BBOX, FALLEN)),
    (2.0, at(2.0, GROUND_BBOX, FALLEN)),
    (4.0, at(4.0, GROUND_BBOX, FALLEN)),
]


def test_fall_row_carries_playable_evidence_ref(tmp_path: Path):
    history = TrackHistory()
    analytics = CameraAnalytics(make_camera(), history=history, evidence_root=tmp_path)
    rows = feed_through_engine(analytics, history, FALL_SCRIPT)

    falls = [row for row in rows if row["kind"] == "fall_detected"]
    assert len(falls) == 1
    row = falls[0]
    assert row["camera_id"] == "CAM_FALL"
    assert row["track_id"] == 1
    assert row["trigger_ts"] == 0.4
    assert row["ts"] == 4.0

    clip = Path(row["evidence_ref"])
    assert clip.is_file() and clip.suffix == ".mp4"
    cap = cv2.VideoCapture(str(clip))
    frames = 0
    while cap.isOpened():
        ok, _ = cap.read()
        if not ok:
            break
        frames += 1
    cap.release()
    assert frames >= 3  # pre-trigger + fall + confirm window
    assert clip.with_suffix(".keypoints.json").is_file()


def test_fall_without_evidence_root_still_emits_row():
    history = TrackHistory()
    analytics = CameraAnalytics(make_camera(), history=history)
    rows = feed_through_engine(analytics, history, FALL_SCRIPT)
    falls = [row for row in rows if row["kind"] == "fall_detected"]
    assert len(falls) == 1
    assert "evidence_ref" not in falls[0]


def test_sitting_stream_produces_no_fall_rows(tmp_path: Path):
    history = TrackHistory()
    analytics = CameraAnalytics(make_camera(), history=history, evidence_root=tmp_path)
    from test_fall import SEATED

    seated_bbox = (60.0, 55.0, 140.0, 205.0)
    rows = feed_through_engine(
        analytics,
        history,
        [
            (0.0, at(0.0)),
            (0.5, at(0.5, seated_bbox, SEATED)),
            (1.0, at(1.0, seated_bbox, SEATED)),
            (2.0, at(2.0, seated_bbox, SEATED)),
            (4.5, at(4.5, seated_bbox, SEATED)),
        ],
    )
    assert [row for row in rows if row["kind"] == "fall_detected"] == []
    assert not list(tmp_path.rglob("*.mp4"))


class FakePoseTracker:
    """Stand-in for PoseTracker.process_frame: scripted TrackedPose lists."""

    produces_pose = True

    def __init__(self, poses_per_frame: list[list[TrackedPose]]) -> None:
        self._script = list(poses_per_frame)
        self.calls = 0

    def process_frame(self, image: np.ndarray) -> list[TrackedPose]:
        poses = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return poses


class FrameStub:
    def __init__(self, ts: float) -> None:
        self.capture_ts = ts
        self.frame_index = int(ts * 10)
        self.image = FRAME


def test_fall_inside_rest_zone_is_suppressed(tmp_path: Path):
    """UR Fall hard-negative mitigation (option a, 2026-08-27): a rest zone
    (bed/berth — lying is expected there) suppresses the fall cascade for
    tracks inside it; a deliberate mattress lie must not fire."""
    history = TrackHistory()
    analytics = CameraAnalytics(
        make_camera(
            {
                "bed": ZoneConfig(
                    name="bed", zone_type=ZoneType.REST, polygon=REST_ZONE_AROUND_PERSON
                )
            }
        ),
        history=history,
        evidence_root=tmp_path,
    )
    rows = feed_through_engine(analytics, history, FALL_SCRIPT)
    assert [row for row in rows if row["kind"] == "fall_detected"] == []
    assert not list(tmp_path.rglob("*.mp4"))


def test_fall_outside_rest_zone_still_fires(tmp_path: Path):
    history = TrackHistory()
    analytics = CameraAnalytics(
        make_camera(
            {"bed": ZoneConfig(name="bed", zone_type=ZoneType.REST, polygon=REST_ZONE_ELSEWHERE)}
        ),
        history=history,
        evidence_root=tmp_path,
    )
    rows = feed_through_engine(analytics, history, FALL_SCRIPT)
    falls = [row for row in rows if row["kind"] == "fall_detected"]
    assert len(falls) == 1
    assert Path(falls[0]["evidence_ref"]).is_file()


def test_run_frame_pose_branch_feeds_keypoint_history():
    history = TrackHistory()
    acc = CameraAccumulator(
        camera=make_camera(),
        reader=object(),
        detector=FakePoseTracker([[sample_to_pose(at(0.0, STANDING_BBOX, STANDING))]]),
        history=history,
        analytics=None,
    )
    run_frame(acc, FrameStub(0.0), detect=True, draw_on=None)
    assert len(history.pose_history(1)) == 1
    assert history.last_sample(1) is not None
