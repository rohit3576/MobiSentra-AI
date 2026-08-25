"""Real-model smoke test (Phase 2, plan §5).

Skipped automatically when ultralytics/weights are unavailable (CI stays
light). Requires a real crowd clip at sample_data/videos/crowd_real_01.mp4
(see SOURCES.md) and yolo26n.pt weights present.

Set MOBISENTRA_VISION_TEST=1 to run locally:
  MOBISENTRA_VISION_TEST=1 uv run pytest tests/test_detection_smoke.py -v -s
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

CLIP = Path(__file__).resolve().parents[1] / "sample_data" / "videos" / "crowd_real_01.mp4"


@pytest.fixture(scope="module")
def detector():
    pytest.importorskip("ultralytics")
    from mobisentra.vision.tracker import DetectorTracker

    return DetectorTracker(model="yolo26n.pt", conf=0.3, classes=[0], track_buffer=60)


@pytest.fixture(scope="module")
def frames():
    import cv2

    assert CLIP.is_file(), f"real crowd clip missing: {CLIP}"
    cap = cv2.VideoCapture(str(CLIP))
    grabbed = []
    for _ in range(30):
        ok, image = cap.read()
        if not ok:
            break
        grabbed.append(image)
    cap.release()
    assert len(grabbed) >= 20, "clip too short"
    return grabbed


def test_persons_detected_and_ids_persist(detector, frames):
    total_by_frame = []
    ids_by_frame = []
    for image in frames:
        people = detector.process_frame(image)
        total_by_frame.append(len(people))
        ids_by_frame.append({p.track_id for p in people})

    avg = sum(total_by_frame) / len(total_by_frame)
    assert avg >= 1.0, f"expected people in crowd clip, avg={avg}"

    stable_frames = sum(
        1 for i in range(1, len(ids_by_frame))
        if ids_by_frame[i] & ids_by_frame[i - 1]
    )
    assert stable_frames >= len(ids_by_frame) * 0.6, (
        f"track IDs not persisting across frames: {stable_frames}/{len(ids_by_frame) - 1}"
    )
    print(
        f"\n[smoke] avg people/frame={avg:.1f}; "
        f"frames with persistent IDs={stable_frames}/{len(ids_by_frame) - 1}"
    )
