#!/usr/bin/env python3
"""Manual rest-zone annotator for UR Fall ADL clips (Phase 4.6, option A).

For each ADL clip: shows a frame near the person's deliberate lying moment,
you click the mattress corners (any order; polygon auto-closes), press
``n`` to save + advance, ``s`` to mark "no zone", ``u`` to undo a click,
``q`` to save + quit. Polygons are stored normalized per clip name in one
JSON that ``fall_benchmark.py --rest-zones file:<path>`` consumes.

Frame choice: the clip's last lying-moment when one is detectable (same
down-position heuristic as the benchmark's derive pass), else 80% of the
clip — the mattress is where the person ends up.

Usage (from edge/, needs a display):
  .venv/bin/python tools/rest_zone_annotator.py \
      --dataset ../mlops/datasets/ur_fall \
      --out ../mlops/datasets/ur_fall/rest-zones-manual.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from mobisentra.analytics.fall_features import torso_angle
from mobisentra.vision.pose import PoseTracker
from mobisentra.vision.track_history import TrackHistory

WINDOW = "rest-zone annotator: click mattress corners, n=next s=skip u=undo q=quit"
PROMPT_HEIGHT = 56


def normalize_polygon(
    points_px: list[tuple[int, int]], width: int, height: int
) -> list[tuple[float, float]]:
    return [(x / width, y / height) for x, y in points_px]


def annotation_frame_index(clip: Path, tracker: PoseTracker | None) -> int:
    """Frame to display: last down-position frame if pose finds one, else 80%."""
    cap = cv2.VideoCapture(str(clip))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fallback = int(total * 0.8)
    if tracker is None:
        cap.release()
        return fallback
    history = TrackHistory()
    index = 0
    last_down = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        poses = tracker.process_frame(frame)
        history.update_poses(index / fps, poses)
        for pose in poses:
            angle = torso_angle(history.pose_history(pose.track_id)[-1])
            if angle is not None and angle < 45.0:
                last_down = index
        index += 1
    cap.release()
    return fallback if last_down is None else last_down


def run_interactive(clips: list[Path], out: Path, tracker: PoseTracker | None) -> None:
    zones: dict[str, list] = {}
    if out.is_file():
        zones = json.loads(out.read_text())
        print(f"[annotator] resuming: {len(zones)} clip(s) already annotated")

    queue = [clip for clip in clips if clip.stem not in zones]
    for clip in queue:
        frame_index = annotation_frame_index(clip, tracker)
        cap = cv2.VideoCapture(str(clip))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            print(f"[annotator] {clip.name}: unreadable, skipping")
            continue
        height, width = frame.shape[:2]
        points: list[tuple[int, int]] = []

        def on_mouse(event, x, y, flags, param, clicks=points):
            if event == cv2.EVENT_LBUTTONDOWN:
                clicks.append((x, y))

        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(WINDOW, on_mouse)
        while True:
            display = frame.copy()
            for px, py in points:
                cv2.circle(display, (px, py), 6, (0, 0, 255), -1)
            if len(points) >= 3:
                outline = np.array(points + [points[0]], dtype=np.int32)
                cv2.polylines(display, [outline], True, (0, 200, 255), 2)
            display[:PROMPT_HEIGHT] = (30, 30, 30)
            cv2.putText(
                display,
                f"{clip.name}  clicks={len(points)}  n=save+next  s=no-zone  u=undo  q=quit",
                (10, 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
            )
            cv2.imshow(WINDOW, display)
            key = cv2.waitKey(30) & 0xFF
            if key == ord("n") and len(points) >= 3:
                zones[clip.stem] = normalize_polygon(points, width, height)
                break
            if key == ord("s"):
                zones[clip.stem] = []
                break
            if key == ord("u") and points:
                points.pop()
            if key == ord("q"):
                cv2.destroyAllWindows()
                break
        cv2.destroyAllWindows()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(zones, indent=2))

    print(f"[annotator] done: {len(zones)} entries -> {out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("../mlops/datasets/ur_fall"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--no-pose",
        action="store_true",
        help="skip the lying-moment lookup (show the 80%% frame); much faster start",
    )
    args = parser.parse_args(argv)
    out = args.out or (args.dataset / "rest-zones-manual.json")
    clips = sorted(args.dataset.glob("adl-*-cam0.mp4"))
    if not clips:
        raise SystemExit(f"no ADL clips under {args.dataset}")
    tracker = None
    if not args.no_pose:
        tracker = PoseTracker(
            model="yolo26n-pose.pt",
            conf=0.3,
            classes=[0],
            tracker="configs/tracktrack-tuned.yaml",
            track_buffer=90,
        )
    run_interactive(clips, out, tracker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
