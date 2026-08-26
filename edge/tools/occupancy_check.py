"""Occupancy gate check: pipeline count vs owner manual count (Step 3.6).

Gate 3 criterion 1 — at 5 sampled frames, the pipeline's in-zone head count
must match a human count within max(1 person, 10%). Two phases:

  measure  run the real detector over a clip, save each sampled frame with
           the zone + detections drawn, write ``measured.json``
  verdict  pair owner-entered manual counts with measured.json, print the
           pass/fail table (exit 1 on any failure)

Usage (from edge/):
  uv run python tools/occupancy_check.py measure \
      --clip sample_data/videos/bus1.mp4 --out runs/occupancy-check
  #   → owner counts people in each saved frame, then:
  uv run python tools/occupancy_check.py verdict \
      --dir runs/occupancy-check --manual 12,14,9,11,13
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import cv2
import numpy as np
import yaml

from mobisentra.analytics.zones import ZoneEngine
from mobisentra.ingestion.config import ZoneConfig, ZoneType

EDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EDGE))

FULL_FRAME_ZONE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


class Sample(TypedDict):
    frame_index: int
    ts: float
    measured: int


@dataclass(frozen=True, slots=True)
class VerdictRow:
    frame_index: int
    measured: int
    manual: int
    delta: int
    tolerance: float
    passes: bool


def sample_frames(total: int, k: int) -> list[int]:
    """Deterministic spread: k indices between 10% and 90% of the clip."""
    if total < 1:
        raise ValueError("no frames to sample")
    last = total - 1
    lo, hi = max(1, total // 10), last - max(1, total // 10)
    n = min(k, hi - lo + 1)
    if n <= 1:
        return [last // 2]
    step = (hi - lo) / (n - 1)
    return [min(hi, max(lo, round(lo + i * step))) for i in range(n)]


def verdict_rows(samples: list[Sample], manual: dict[int, int]) -> list[VerdictRow]:
    rows: list[VerdictRow] = []
    for sample in samples:
        counted = manual[sample["frame_index"]]
        tolerance = max(1, round(0.10 * counted, 10))
        delta = abs(sample["measured"] - counted)
        rows.append(
            VerdictRow(
                frame_index=sample["frame_index"],
                measured=sample["measured"],
                manual=counted,
                delta=delta,
                tolerance=tolerance,
                passes=delta <= tolerance,
            )
        )
    return rows


def measure(clip: Path, out: Path, samples_k: int, detection_cfg: dict) -> list[Sample]:
    from mobisentra.vision.tracker import DetectorTracker

    out.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(clip))
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    indices = sample_frames(total, samples_k)
    print(f"[occupancy-check] {clip.name}: {total} frames, sampling {indices}")

    zone = ZoneConfig(
        name="count_zone",
        zone_type=ZoneType.OCCUPANCY,
        polygon=FULL_FRAME_ZONE,
        max_capacity=100,
    )
    engine = ZoneEngine({"count_zone": zone})
    detector = DetectorTracker(**detection_cfg)

    samples: list[Sample] = []
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok or index > indices[-1]:
            break
        if index in indices:
            people = detector.process_frame(frame)
            membership = engine.update(frame, people)
            count = len(membership["count_zone"])
            display = frame.copy()
            height, width = display.shape[:2]
            points = np.array(
                [[round(x * width), round(y * height)] for x, y in zone.polygon], dtype=np.int32
            )
            cv2.polylines(display, [points], isClosed=True, color=(255, 160, 0), thickness=2)
            for p in people:
                x1, y1, x2, y2 = (int(v) for v in p.bbox)
                cv2.rectangle(display, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.imwrite(str(out / f"frame_{index:05d}.jpg"), display)
            samples.append(Sample(frame_index=index, ts=float(index), measured=count))
            print(f"[occupancy-check] frame {index}: measured {count}")
        else:
            detector.process_frame(frame)  # keep tracker state warm between samples
        index += 1
    capture.release()
    (out / "measured.json").write_text(
        json.dumps({"clip": str(clip), "samples": samples}, indent=2)
    )
    print(f"[occupancy-check] wrote {out / 'measured.json'} — now count heads in the JPGs")
    return samples


def print_verdict(dir_: Path, manual_counts: list[int]) -> int:
    data = json.loads((dir_ / "measured.json").read_text())
    manual = {
        sample["frame_index"]: count
        for sample, count in zip(data["samples"], manual_counts, strict=True)
    }
    rows = verdict_rows(data["samples"], manual)
    print(f"\n{'frame':>7} {'measured':>9} {'manual':>7} {'delta':>6} {'tol':>5} verdict")
    for row in rows:
        print(
            f"{row.frame_index:>7} {row.measured:>9} {row.manual:>7} "
            f"{row.delta:>6} {row.tolerance:>5} {'PASS' if row.passes else 'FAIL'}"
        )
    all_pass = all(row.passes for row in rows)
    print(f"\nGATE 3 criterion 1 ({len(rows)} frames): {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


def load_detection_cfg() -> dict:
    config = EDGE / "configs" / "detection.yaml"
    return yaml.safe_load(config.read_text()) or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_measure = sub.add_parser("measure", help="sample frames + measured counts")
    p_measure.add_argument("--clip", type=Path, required=True)
    p_measure.add_argument("--out", type=Path, default=Path("runs/occupancy-check"))
    p_measure.add_argument("--samples", type=int, default=5)

    p_verdict = sub.add_parser("verdict", help="compare manual counts")
    p_verdict.add_argument("--dir", type=Path, default=Path("runs/occupancy-check"))
    p_verdict.add_argument("--manual", required=True, help="counts in frame order: 12,14,9,11,13")

    args = parser.parse_args(argv)
    if args.command == "measure":
        measure(args.clip, args.out, args.samples, load_detection_cfg())
        return 0
    manual_counts = [int(v) for v in args.manual.split(",")]
    return print_verdict(args.dir, manual_counts)


if __name__ == "__main__":
    sys.exit(main())
