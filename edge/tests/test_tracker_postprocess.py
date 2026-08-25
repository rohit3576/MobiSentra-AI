"""postprocess_results tests with fake Results objects (no ultralytics)."""

from __future__ import annotations

import numpy as np

from mobisentra.vision.tracker import postprocess_results


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


class FakeResult:
    def __init__(self, boxes) -> None:
        self.boxes = boxes


def test_drops_frames_without_track_ids():
    result = FakeResult(FakeBoxes([[0, 0, 10, 10]], [0.9], ids=None, cls=[0]))
    assert postprocess_results(result, tracked_classes=[0]) == []


def test_converts_tracked_persons():
    result = FakeResult(
        FakeBoxes(
            [[0, 0, 10, 20], [30, 5, 40, 25]],
            [0.9, 0.5],
            ids=[7, 8],
            cls=[0, 0],
        )
    )
    people = postprocess_results(result, tracked_classes=[0])
    assert [(p.track_id, p.confidence) for p in people] == [(7, 0.9), (8, 0.5)]
    assert people[0].bbox == (0.0, 0.0, 10.0, 20.0)


def test_filters_non_tracked_classes():
    result = FakeResult(
        FakeBoxes(
            [[0, 0, 10, 20], [30, 5, 40, 25], [50, 5, 60, 25]],
            [0.9, 0.8, 0.7],
            ids=[1, 2, 3],
            cls=[0, 24, 26],
        )
    )
    people = postprocess_results(result, tracked_classes=[0])
    assert [p.track_id for p in people] == [1]


def test_cls_none_treats_all_as_tracked():
    result = FakeResult(FakeBoxes([[0, 0, 10, 20]], [0.9], ids=[4], cls=None))
    people = postprocess_results(result, tracked_classes=[0])
    assert [p.track_id for p in people] == [4]


def test_none_boxes():
    class EmptyResult:
        boxes = None

    assert postprocess_results(EmptyResult(), tracked_classes=[0]) == []
