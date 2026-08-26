"""postprocess_pose_results tests with fake Results objects (no ultralytics).

Mirrors tests/test_tracker_postprocess.py: FakeTensor/FakeBoxes/FakeResult,
plus FakeKeypoints carrying (n, 17, 3) xyc data.
"""

from __future__ import annotations

import numpy as np
import pytest

from mobisentra.vision.pose import (
    N_KEYPOINTS,
    KeypointIndex,
    PoseError,
    postprocess_pose_results,
)


class FakeTensor:
    def __init__(self, array: np.ndarray) -> None:
        self._array = array

    def cpu(self):
        return self

    def numpy(self):
        return self._array


class FakeBoxes:
    def __init__(self, xyxy, conf, ids, cls) -> None:
        self.xyxy = FakeTensor(np.array(xyxy, dtype=float))
        self.conf = FakeTensor(np.array(conf, dtype=float))
        self.id = None if ids is None else FakeTensor(np.array(ids, dtype=int))
        self.cls = None if cls is None else FakeTensor(np.array(cls, dtype=int))


class FakeKeypoints:
    def __init__(self, data) -> None:
        self.data = None if data is None else FakeTensor(np.array(data, dtype=float))


class FakePoseResult:
    def __init__(self, boxes, keypoints) -> None:
        self.boxes = boxes
        self.keypoints = keypoints


def keypoints_for(n: int) -> list[list[list[float]]]:
    """n people × 17 keypoints, values encode (person, joint) for assertions."""
    return [
        [[float(person), float(joint), 0.9] for joint in range(N_KEYPOINTS)] for person in range(n)
    ]


def test_converts_tracked_poses_with_17_keypoints():
    result = FakePoseResult(
        FakeBoxes([[0, 0, 10, 20], [30, 5, 40, 25]], [0.9, 0.5], ids=[7, 8], cls=[0, 0]),
        FakeKeypoints(keypoints_for(2)),
    )
    poses = postprocess_pose_results(result, tracked_classes=[0])
    assert [(p.track_id, p.confidence) for p in poses] == [(7, 0.9), (8, 0.5)]
    assert poses[0].bbox == (0.0, 0.0, 10.0, 20.0)
    assert len(poses[0].keypoints) == N_KEYPOINTS
    assert poses[0].keypoints[KeypointIndex.LEFT_HIP] == (0.0, 11.0, 0.9)
    assert poses[1].keypoints[KeypointIndex.NOSE] == (1.0, 0.0, 0.9)


def test_drops_frames_without_track_ids():
    result = FakePoseResult(
        FakeBoxes([[0, 0, 10, 10]], [0.9], ids=None, cls=[0]),
        FakeKeypoints(keypoints_for(1)),
    )
    assert postprocess_pose_results(result, tracked_classes=[0]) == []


def test_filters_non_tracked_classes():
    result = FakePoseResult(
        FakeBoxes(
            [[0, 0, 10, 20], [30, 5, 40, 25], [50, 5, 60, 25]],
            [0.9, 0.8, 0.7],
            ids=[1, 2, 3],
            cls=[0, 24, 26],
        ),
        FakeKeypoints(keypoints_for(3)),
    )
    poses = postprocess_pose_results(result, tracked_classes=[0])
    assert [p.track_id for p in poses] == [1]


def test_missing_keypoints_drops_all():
    result = FakePoseResult(
        FakeBoxes([[0, 0, 10, 20]], [0.9], ids=[7], cls=[0]),
        FakeKeypoints(None),
    )
    assert postprocess_pose_results(result, tracked_classes=[0]) == []


def test_keypoint_count_mismatch_raises():
    bad = [[[0.0, 0.0, 0.9]] for _ in range(1)]  # 1 keypoint instead of 17
    result = FakePoseResult(
        FakeBoxes([[0, 0, 10, 20]], [0.9], ids=[7], cls=[0]),
        FakeKeypoints(bad),
    )
    with pytest.raises(PoseError):
        postprocess_pose_results(result, tracked_classes=[0])


def test_keypoint_index_is_complete_coco_17():
    assert len(KeypointIndex) == N_KEYPOINTS == 17
    assert KeypointIndex.NOSE == 0
    assert KeypointIndex.LEFT_SHOULDER == 5
    assert KeypointIndex.RIGHT_SHOULDER == 6
    assert KeypointIndex.LEFT_HIP == 11
    assert KeypointIndex.RIGHT_HIP == 12
    assert KeypointIndex.LEFT_ANKLE == 15
    assert KeypointIndex.RIGHT_ANKLE == 16
