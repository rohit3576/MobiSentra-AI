"""Zone editor: draw a polygon on a saved frame → normalized YAML snippet.

Gate 3 utility (Phase 3, Step 3.5): click zone points on a real frame so
polygons match the actual camera view instead of guessed coordinates.
The exported ``zones:`` snippet pastes directly under a camera in the
registry and round-trips through the parser + ZoneEngine (see
tests/test_zone_editor.py).

Usage (from edge/):
  uv run python tools/zone_editor.py sample_data/videos/bus1.mp4 \
      --name door_roi --type door
  uv run python tools/zone_editor.py frame.jpg --name bus_area \
      --type occupancy --max-capacity 50 --out configs/zones-snippet.yaml

Controls: click = add point · r = reset · Enter = close polygon · Esc = cancel.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np
import yaml

from mobisentra.ingestion.config import ConfigError, ZoneType

EDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EDGE))

ENTER_KEYS = (13, 10)
ESC_KEY = 27


def normalize_points(
    points: Sequence[tuple[int, int]], width: int, height: int
) -> tuple[tuple[float, float], ...]:
    """Pixel clicks → registry-normalized [0.0, 1.0] coordinates (4 decimals)."""
    return tuple((round(x / width, 4), round(y / height, 4)) for x, y in points)


def build_zones_yaml(
    *,
    name: str,
    zone_type: str,
    points: Sequence[tuple[float, float]],
    max_capacity: int | None = None,
) -> str:
    """Build a paste-ready ``zones:`` snippet from normalized points."""
    if zone_type not in set(ZoneType):
        raise ConfigError(f"unknown zone type: {zone_type!r}")
    if len(points) < 3:
        raise ValueError("a zone polygon needs at least 3 points")
    zone: dict[str, object] = {
        "type": zone_type,
        "polygon": [[x, y] for x, y in points],
    }
    if max_capacity is not None:
        zone["max_capacity"] = max_capacity
    return yaml.safe_dump({"zones": {name: zone}}, sort_keys=False)


def collect_polygon(image) -> list[tuple[int, int]] | None:
    """Interactive loop: click points, r reset, Enter close, Esc cancel."""
    points: list[tuple[int, int]] = []
    window = "zone editor — click: point | r: reset | Enter: close | Esc: cancel"

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((x, y))

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)
    while True:
        display = image.copy()
        for index, (x, y) in enumerate(points):
            cv2.circle(display, (x, y), 4, (0, 255, 0), -1)
            cv2.putText(
                display, str(index), (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1
            )
        if len(points) >= 2:
            cv2.polylines(display, [np.array(points)], False, (0, 200, 0), 2)
        if len(points) >= 3:
            cv2.polylines(display, [np.array(points + [points[0]])], False, (0, 120, 0), 1)
        cv2.imshow(window, display)
        key = cv2.waitKey(30) & 0xFF
        if key == ord("r"):
            points.clear()
        elif key in ENTER_KEYS:
            if len(points) >= 3:
                cv2.destroyAllWindows()
                return points
            print("[zone-editor] need at least 3 points to close a polygon")
        elif key == ESC_KEY:
            cv2.destroyAllWindows()
            return None


def load_frame(path: Path, frame_index: int):
    """First frame of a video (or the image itself)."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise SystemExit(f"cannot open video/image: {path}")
    if frame_index > 0:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise SystemExit(f"no frame readable at index {frame_index}: {path}")
    return frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MobiSentra zone editor")
    parser.add_argument("media", type=Path, help="video or image to draw on")
    parser.add_argument("--frame-index", type=int, default=0)
    parser.add_argument("--name", default="zone_1", help="zone name in the snippet")
    parser.add_argument("--type", dest="zone_type", default="restricted", choices=list(ZoneType))
    parser.add_argument("--max-capacity", type=int, default=None, help="occupancy zones only")
    parser.add_argument("--out", type=Path, default=None, help="write snippet here (else stdout)")
    args = parser.parse_args(argv)

    frame = load_frame(args.media, args.frame_index)
    height, width = frame.shape[:2]
    print(f"[zone-editor] {args.media.name} {width}x{height} — click the {args.name} polygon")

    points = collect_polygon(frame)
    if points is None:
        print("[zone-editor] cancelled")
        return 1

    snippet = build_zones_yaml(
        name=args.name,
        zone_type=args.zone_type,
        points=normalize_points(points, width, height),
        max_capacity=args.max_capacity,
    )
    if args.out is not None:
        args.out.write_text(snippet)
        print(f"[zone-editor] snippet written to {args.out}")
    else:
        print(snippet, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
