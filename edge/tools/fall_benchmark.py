#!/usr/bin/env python3
"""Gate-4 fall benchmark on UR Fall (Phase 4, Steps 4.5–4.6).

Runs the production composition — ``CameraAnalytics`` (zones + fall cascade
+ evidence plumbing) over ``PoseTracker``/``TrackHistory`` — single-pass
over every clip, then a SETTLE phase: the frame clock advances with no new
detections, the production occlusion semantics ("no new evidence is not
recovery"), so pending confirmations resolve. UR Fall reality (measured
2026-08-27): fall clips are cut tight (median 3.1 s, 29/30 under 6.5 s) —
a 3 s confirm cannot elapse inside the footage; settle is what measures
production T=3.0 on this dataset.

``--rest-zones derive`` emulates operator bed-marking for the hard
negatives: pass 1 localizes each ADL clip's deliberate lying spot (the
mattress), pass 2 runs the cascade with that REST zone configured —
tracks inside it are suppressed (Phase 4.6 option a). Fall clips get NO
zone: their lying spots are floors, not beds — marking them would be
marking the answer.

Metrics:
- falls: fired (fall_detected event), trigger-only, missed
- ADL: false positives, split in-footage vs settle-only, per footage hour

Usage (from edge/):
    .venv/bin/python tools/fall_benchmark.py \
        --dataset ../mlops/datasets/ur_fall [--rest-zones derive] \
        [--confirm 3.0] [--settle 3.5] [--velocity 0.75] [--velocity-high 2.0] \
        [--limit 5] [--trace fall-01-cam0] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from mobisentra.analytics.engine import CameraAnalytics
from mobisentra.analytics.fall import FallConfig
from mobisentra.analytics.fall_features import torso_angle
from mobisentra.ingestion.config import CameraConfig, ZoneConfig, ZoneType
from mobisentra.vision.pose import PoseTracker
from mobisentra.vision.track_history import TrackHistory
from mobisentra.vision.tracker import TrackedPerson

REST_ZONE_HALF_EXTENT = 0.25
REST_ZONE_HALF_EXTENT_UP = 0.35  # the descent happens ABOVE the lying spot
REST_ZONE_HALF_EXTENT_DOWN = 0.15


@dataclass
class ClipResult:
    name: str
    split: str
    duration_s: float
    rest_zone: tuple[tuple[float, float], ...] | None = None
    zone_origin: str | None = None
    fired: int = 0
    triggered: int = 0
    events: list[dict] = field(default_factory=list)


def derive_rest_zone(path: Path, tracker: PoseTracker) -> tuple[tuple[float, float], ...] | None:
    """Mark the mattress for an ADL clip: median down-position of the last
    2 s → normalized square. The median point is the bbox BOTTOM-CENTER —
    the same anchor zone membership tests (feet), so derivation and
    suppression agree geometrically even when pose jitter moves hips.
    Pass-1 heuristic standing in for an operator watching the clip once."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1.0
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1.0
    history = TrackHistory()
    track_ids: set[int] = set()
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ts = index / fps
        poses = tracker.process_frame(frame)
        history.update_poses(ts, poses)
        track_ids.update(p.track_id for p in poses)
        index += 1
    cap.release()

    down_points: list[tuple[float, float]] = []
    horizon = index / fps - 2.0
    for track_id in sorted(track_ids):
        for sample in history.pose_history(track_id):
            if sample.ts < horizon:
                continue
            angle = torso_angle(sample)
            aspect = (sample.bbox[2] - sample.bbox[0]) / max(1.0, sample.bbox[3] - sample.bbox[1])
            if (angle is None or angle >= 45.0) and aspect < 1.1:
                continue
            down_points.append(((sample.bbox[0] + sample.bbox[2]) / 2, sample.bbox[3]))
    if len(down_points) < 5:
        return None
    xs = sorted(x for x, _ in down_points)
    ys = sorted(y for _, y in down_points)
    cx, cy = xs[len(xs) // 2] / width, ys[len(ys) // 2] / height
    return _square(cx, cy)


def _square(cx: float, cy: float) -> tuple[tuple[float, float], ...]:
    return (
        (max(0.0, cx - REST_ZONE_HALF_EXTENT), max(0.0, cy - REST_ZONE_HALF_EXTENT_UP)),
        (min(1.0, cx + REST_ZONE_HALF_EXTENT), max(0.0, cy - REST_ZONE_HALF_EXTENT_UP)),
        (
            min(1.0, cx + REST_ZONE_HALF_EXTENT),
            min(1.0, cy + REST_ZONE_HALF_EXTENT_DOWN),
        ),
        (
            max(0.0, cx - REST_ZONE_HALF_EXTENT),
            min(1.0, cy + REST_ZONE_HALF_EXTENT_DOWN),
        ),
    )


def alert_rest_zone(
    result: ClipResult, path: Path, tracker: PoseTracker
) -> tuple[tuple[float, float], ...] | None:
    """Mark a rest zone at a false alert's own trigger location and let the
    clip replay against it — the commissioning loop an operator runs for
    real (see alert → mark the bed → alert stops)."""
    if not result.events:
        return None
    cap = cv2.VideoCapture(str(path))
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1.0
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1.0
    cap.release()
    event = result.events[0]
    history = _trigger_pose_snapshot(path, tracker, event["trigger_ts"], event["track_id"])
    if history is None:
        return None
    x1, _, x2, y2 = history
    return _square((x1 + x2) / 2 / width, y2 / height)


def _trigger_pose_snapshot(
    path: Path, tracker: PoseTracker, trigger_ts: float, track_id: int
) -> tuple[float, float, float, float] | None:
    """Replay to the trigger moment and grab the pose closest to it. Matches
    by TIME, not track id: the replay runs on shared tracker state and
    re-detects the same person under new ids (benchmark clips are
    single-person, so the pose nearest the trigger IS the trigger track)."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    index = 0
    best: tuple[float, tuple[float, float, float, float]] | None = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ts = index / fps
        if ts > trigger_ts + 0.2:
            break
        for pose in tracker.process_frame(frame):
            if abs(ts - trigger_ts) <= 0.2:
                best = (abs(ts - trigger_ts), pose.bbox)
        index += 1
    cap.release()
    return best[1] if best else None


def run_clip(
    path: Path,
    tracker: PoseTracker,
    fall_config: FallConfig,
    settle_s: float,
    rest_zone: tuple[tuple[float, float], ...] | None,
    trace: bool,
) -> ClipResult:
    result = ClipResult(
        name=path.stem,
        split="falls" if "fall-" in path.stem else "adl",
        duration_s=0.0,
        rest_zone=rest_zone,
    )
    zones = {}
    if rest_zone is not None:
        zones["rest_area"] = ZoneConfig(
            name="rest_area", zone_type=ZoneType.REST, polygon=rest_zone
        )
    camera = CameraConfig(id=path.stem, source=str(path), vehicle_id="BENCH", zones=zones)
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    history = TrackHistory()
    analytics = CameraAnalytics(camera, history=history, fall_config=fall_config)

    events: list[dict] = []
    last_frame: np.ndarray | None = None
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ts = index / fps
        poses = tracker.process_frame(frame)
        people = [
            TrackedPerson(track_id=p.track_id, bbox=p.bbox, confidence=p.confidence) for p in poses
        ]
        history.update(ts, people)
        history.update_poses(ts, poses)
        events.extend(_fall_rows(analytics.process(ts, frame, people)))
        last_frame = frame
        if trace and index % 15 == 0:
            pending = analytics.pending_fall_track_ids()
            if pending or poses:
                print(f"  t={ts:5.2f} tracks={[p.track_id for p in poses]} pending={pending}")
        index += 1
    cap.release()
    result.duration_s = index / fps

    if last_frame is not None:
        for step in range(1, int(settle_s / 0.1) + 1):
            events.extend(
                _fall_rows(analytics.process(result.duration_s + step * 0.1, last_frame, []))
            )
    result.fired = len(events)
    result.triggered = len(analytics.pending_fall_track_ids())
    result.events = events
    return result


def _fall_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "track_id": row["track_id"],
            "trigger_ts": row["trigger_ts"],
            "ts": row["ts"],
            "confidence": row["confidence"],
        }
        for row in rows
        if row["kind"] == "fall_detected"
    ]


def load_clips(dataset: Path, split: str = "all") -> list[Path]:
    patterns = {
        "all": ("fall-*-cam0.mp4", "adl-*-cam0.mp4"),
        "falls": ("fall-*-cam0.mp4",),
        "adl": ("adl-*-cam0.mp4",),
    }[split]
    clips = [clip for pattern in patterns for clip in sorted(dataset.glob(pattern))]
    if not clips:
        raise SystemExit(f"no clips under {dataset} — run mlops/datasets/download_ur_fall.py first")
    return clips


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("../mlops/datasets/ur_fall"))
    parser.add_argument("--model", default="yolo26n-pose.pt")
    parser.add_argument("--conf", type=float, default=0.3)
    parser.add_argument("--confirm", type=float, default=3.0)
    parser.add_argument("--settle", type=float, default=3.5)
    parser.add_argument("--velocity", type=float, default=0.75)
    parser.add_argument("--velocity-high", type=float, default=2.0)
    parser.add_argument(
        "--rest-zones",
        choices=("off", "derive"),
        default="off",
        help="derive: emulate operator bed-marking per ADL clip (pass-1 mattress localization)",
    )
    parser.add_argument("--split", choices=("all", "falls", "adl"), default="all")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--trace", default="")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    clips = load_clips(args.dataset, args.split)
    if args.limit:
        clips = clips[: args.limit]
    fall_config = FallConfig(
        confirm_seconds=args.confirm,
        velocity_body_heights_per_s=args.velocity,
        velocity_high_bar_bh_s=args.velocity_high,
    )
    tracker = PoseTracker(
        model=args.model,
        conf=args.conf,
        classes=[0],
        tracker="configs/tracktrack-tuned.yaml",
        track_buffer=90,
    )

    results: list[ClipResult] = []
    started = time.monotonic()
    for clip in clips:
        rest_zone = None
        zone_origin = None
        if args.rest_zones == "derive" and "adl-" in clip.stem:
            rest_zone = derive_rest_zone(clip, tracker)
            zone_origin = "derived" if rest_zone else None
        trace = bool(args.trace) and args.trace in clip.stem
        result = run_clip(clip, tracker, fall_config, args.settle, rest_zone, trace)
        if args.rest_zones == "derive" and "adl-" in clip.stem and result.fired:
            alert_zone = alert_rest_zone(result, clip, tracker)
            if alert_zone is not None:
                result = run_clip(clip, tracker, fall_config, args.settle, alert_zone, trace)
                zone_origin = "alert"
        result.zone_origin = zone_origin
        results.append(result)
        zone = f" zone={zone_origin or '-'}"
        flag = "FIRE" if result.fired else ("trig" if result.triggered else "----")
        print(
            f"[{flag}] {result.name:<18} {result.duration_s:5.1f}s "
            f"events={result.fired} pending={result.triggered}{zone}"
        )

    falls = [r for r in results if r.split == "falls"]
    adls = [r for r in results if r.split == "adl"]
    fired = sum(1 for r in falls if r.fired)
    triggered_only = sum(1 for r in falls if r.triggered and not r.fired)
    missed = len(falls) - fired - triggered_only
    fp_footage = sum(1 for r in adls for e in r.events if e["ts"] <= r.duration_s + 1e-6)
    fp_settle = sum(1 for r in adls for e in r.events if e["ts"] > r.duration_s + 1e-6)
    footage_hours = sum(r.duration_s for r in adls) / 3600.0
    zoned = sum(1 for r in adls if r.rest_zone is not None)
    zoned_alert = sum(1 for r in adls if r.zone_origin == "alert")

    print(
        f"\n=== UR Fall benchmark (confirm={args.confirm}s, settle={args.settle}s, "
        f"rest-zones={args.rest_zones}) ==="
    )
    print(
        f"falls: {len(falls)}  fired={fired} ({100 * fired / max(1, len(falls)):.0f}%)  "
        f"trigger-only={triggered_only}  missed={missed}"
    )
    print(
        f"adl:   {len(adls)} (zoned: {zoned}, alert-marked: {zoned_alert})"
        f"  fp_in_footage={fp_footage}  fp_settle_only={fp_settle}  "
        f"footage={footage_hours * 60:.1f}min"
    )
    fp_hr = fp_footage / footage_hours if footage_hours else 0.0
    fp_hr_all = (fp_footage + fp_settle) / footage_hours if footage_hours else 0.0
    print(f"fp/hr over footage: {fp_hr:.1f} (incl. settle-only: {fp_hr_all:.1f})")
    print(f"wall: {time.monotonic() - started:.0f}s")

    if args.json:
        payload = {
            "confirm_s": args.confirm,
            "settle_s": args.settle,
            "rest_zones": args.rest_zones,
            "model": args.model,
            "falls": {
                "total": len(falls),
                "fired": fired,
                "trigger_only": triggered_only,
                "missed": missed,
            },
            "adl": {
                "total": len(adls),
                "rest_zoned": zoned,
                "fp_footage": fp_footage,
                "fp_settle": fp_settle,
                "footage_hours": footage_hours,
            },
            "clips": [vars(r) for r in results],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2))
        print(f"results -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
