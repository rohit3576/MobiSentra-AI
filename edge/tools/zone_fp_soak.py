"""Empty-zone false-positive soak (Phase 3, Step 3.6 criterion 2).

Gate 3: over 30 minutes of footage with configured zones that stay empty,
the pipeline must emit ZERO zone events (no occupancy band flips, no
restricted entries, no door obstructions).

Two phases:

  site   one detection pass over the footage pool → union grid of every
         person bbox → largest empty rectangle (classic maximal-rectangle
         scan) → that region hosts all three zone types for the soak
  soak   loop the pool until the target duration (clock runs continuously
         across loops — documented limitation: ~49 s of unique real footage
         repeated; the exposure being tested is detector noise + zone logic
         over 30 min of stream time), run CameraAnalytics (the production
         zone composition), count zone events

Any event is a false positive by construction: the zone region was empty
in the siting pass and nobody enters it during the soak unless the
detector hallucinates — which is precisely what this gate measures.

Usage (from edge/):
  .venv/bin/python tools/zone_fp_soak.py site --pool sample_data/videos/*.mp4
  .venv/bin/python tools/zone_fp_soak.py soak --minutes 30 --analyze-every 2 \
      --report runs/zone-fp-soak.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

from mobisentra.analytics.engine import CameraAnalytics
from mobisentra.ingestion.config import (
    CameraConfig,
    Thresholds,
    ZoneConfig,
    ZoneType,
)
from mobisentra.vision.tracker import DetectorTracker

GRID_COLS = 24
GRID_ROWS = 14
MIN_ZONE_FRACTION = 0.08


def occupied_grid(cells_seen: set[tuple[int, int]]) -> list[list[bool]]:
    return [[(col, row) in cells_seen for col in range(GRID_COLS)] for row in range(GRID_ROWS)]


def largest_empty_rectangle(grid: list[list[bool]]) -> tuple[int, int, int, int] | None:
    """Max-area all-False rectangle in a boolean grid (row-major scan with
    histogram heights). Returns (row, col, rows, cols) or None if no cell
    is empty."""
    heights = [0] * GRID_COLS
    best: tuple[int, int, int, int] | None = None
    best_area = 0
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            heights[col] = 0 if grid[row][col] else heights[col] + 1
        stack: list[tuple[int, int]] = []
        for col in range(GRID_COLS + 1):
            height = heights[col] if col < GRID_COLS else 0
            start = col
            while stack and stack[-1][1] >= height:
                begin, bar = stack.pop()
                area = bar * (col - begin)
                if area > best_area:
                    best_area = area
                    best = (row - bar + 1, begin, bar, col - begin)
                start = begin
            stack.append((start, height))
    return best if best_area else None


def rectangle_to_polygon(
    rect: tuple[int, int, int, int], width: int, height: int, inset: float = 0.5
) -> tuple[tuple[float, float], ...]:
    row, col, rows, cols = rect
    x1 = (col + inset) * width / GRID_COLS
    x2 = (col + cols - inset) * width / GRID_COLS
    y1 = (row + inset) * height / GRID_ROWS
    y2 = (row + rows - inset) * height / GRID_ROWS
    return (
        (x1 / width, y1 / height),
        (x2 / width, y1 / height),
        (x2 / width, y2 / height),
        (x1 / width, y2 / height),
    )


def site_zones(pool: list[Path], model: str, conf: float) -> dict:
    detector = DetectorTracker(model=model, conf=conf, classes=[0])
    cells: set[tuple[int, int]] = set()
    frame_shape: tuple[int, int] | None = None
    for clip in pool:
        cap = cv2.VideoCapture(str(clip))
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_shape = frame.shape[:2]
            for person in detector.process_frame(frame):
                x1, y1, x2, y2 = person.bbox
                for cell_row in range(GRID_ROWS):
                    for cell_col in range(GRID_COLS):
                        cx1 = cell_col * frame_shape[1] / GRID_COLS
                        cx2 = (cell_col + 1) * frame_shape[1] / GRID_COLS
                        cy1 = cell_row * frame_shape[0] / GRID_ROWS
                        cy2 = (cell_row + 1) * frame_shape[0] / GRID_ROWS
                        if x1 < cx2 and x2 > cx1 and y1 < cy2 and y2 > cy1:
                            cells.add((cell_col, cell_row))
        cap.release()
    if frame_shape is None:
        raise SystemExit("no frames read from pool")
    rect = largest_empty_rectangle(occupied_grid(cells))
    if rect is None:
        raise SystemExit("every grid cell saw a person — no empty region")
    _, _, rows, cols = rect
    area_fraction = (rows * cols) / (GRID_ROWS * GRID_COLS)
    if area_fraction < MIN_ZONE_FRACTION:
        raise SystemExit(
            f"largest empty region covers only {area_fraction:.0%} of the frame "
            f"(need ≥ {MIN_ZONE_FRACTION:.0%})"
        )
    polygon = rectangle_to_polygon(rect, frame_shape[1], frame_shape[0])
    return {"polygon": polygon, "area_fraction": area_fraction, "grid": rect}


def soak(
    pool: list[Path],
    polygon: tuple[tuple[float, float], ...],
    minutes: float,
    analyze_every: int,
    model: str,
    conf: float,
    report: Path | None,
) -> int:
    zones = {
        "soak_occupancy": ZoneConfig(
            name="soak_occupancy",
            zone_type=ZoneType.OCCUPANCY,
            polygon=polygon,
            max_capacity=50,
        ),
        "soak_restricted": ZoneConfig(
            name="soak_restricted", zone_type=ZoneType.RESTRICTED, polygon=polygon
        ),
        "soak_door": ZoneConfig(name="soak_door", zone_type=ZoneType.DOOR, polygon=polygon),
    }
    camera = CameraConfig(
        id="ZONE_FP_SOAK",
        source="pool",
        vehicle_id="SOAK",
        zones=zones,
        thresholds=Thresholds(
            occupancy_confirm_frames=30,
            restricted_loiter_seconds=5.0,
            door_obstruct_seconds=3.0,
        ),
    )
    detector = DetectorTracker(model=model, conf=conf, classes=[0])
    analytics = CameraAnalytics(camera)

    target_s = minutes * 60.0
    clock = 0.0
    events: list[dict] = []
    analyzed = 0
    loops = 0
    started = time.monotonic()
    while clock < target_s:
        for clip in pool:
            cap = cv2.VideoCapture(str(clip))
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            frame_index = 0
            while True:
                ok, frame = cap.read()
                if not ok or clock >= target_s:
                    break
                clock += 1.0 / fps
                if frame_index % analyze_every == 0:
                    people = detector.process_frame(frame)
                    for row in analytics.process(clock, frame, people):
                        events.append({"ts": clock, **row})
                    analyzed += 1
                frame_index += 1
            cap.release()
            if clock >= target_s:
                break
        loops += 1

    wall_s = time.monotonic() - started

    fps_effective = analyzed / clock if clock else 0.0
    print(
        f"[soak] {clock / 60:.1f} min stream ({loops} loop(s), "
        f"{analyzed} analyzed frames ≈ {fps_effective:.1f}/s, wall {wall_s / 60:.0f} min) "
        f"-> {len(events)} zone events"
    )
    for event in events[:10]:
        print(f"  FP @{event['ts']:.1f}s {event['kind']} {event.get('zone', '')}")
    if len(events) > 10:
        print(f"  … and {len(events) - 10} more")
    verdict = "PASS" if not events else "FAIL"
    print(f"GATE 3 criterion 2 (empty-zone FP): {verdict}")
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "minutes": clock / 60.0,
                    "loops": loops,
                    "analyzed_frames": analyzed,
                    "zone_polygon": polygon,
                    "events": events,
                    "verdict": verdict,
                },
                indent=2,
                default=str,
            )
        )
        print(f"report -> {report}")
    return 0 if not events else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_site = sub.add_parser("site", help="find the empty region for the zones")
    p_site.add_argument("--pool", type=Path, nargs="+", required=True)
    p_site.add_argument("--model", default="yolo26n.pt")
    p_site.add_argument("--conf", type=float, default=0.3)
    p_site.add_argument("--out", type=Path, default=Path("runs/zone-soak-zones.json"))

    p_soak = sub.add_parser("soak", help="run the timed empty-zone FP check")
    p_soak.add_argument("--pool", type=Path, nargs="+", required=True)
    p_soak.add_argument("--polygon", type=Path, default=Path("runs/zone-soak-zones.json"))
    p_soak.add_argument("--minutes", type=float, default=30.0)
    p_soak.add_argument("--analyze-every", type=int, default=2)
    p_soak.add_argument("--model", default="yolo26n.pt")
    p_soak.add_argument("--conf", type=float, default=0.3)
    p_soak.add_argument("--report", type=Path, default=None)

    args = parser.parse_args(argv)
    if args.command == "site":
        sited = site_zones(args.pool, args.model, args.conf)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(sited, indent=2))
        print(
            f"[site] empty region {sited['grid']} covers {sited['area_fraction']:.0%} "
            f"of frame -> {args.out}"
        )
        return 0
    data = json.loads(args.polygon.read_text())
    polygon = tuple(tuple(point) for point in data["polygon"])
    return soak(
        args.pool,
        polygon,
        args.minutes,
        args.analyze_every,
        args.model,
        args.conf,
        args.report,
    )


if __name__ == "__main__":
    sys.exit(main())
