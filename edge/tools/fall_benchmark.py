#!/usr/bin/env python3
"""Gate-4 fall benchmark on UR Fall (Phase 4, Step 4.5).

Runs the production cascade (PoseTracker → TrackHistory → FallDetector,
the same composition ``CameraAnalytics`` drives) single-pass over every
clip in a downloaded dataset folder, then a SETTLE phase: the frame clock
advances with no new detections — the production occlusion semantics
("no new evidence is not recovery") — so pending confirmations resolve.

UR Fall reality (measured 2026-08-27): fall clips are cut tight (median
3.1 s, 29/30 under 6.5 s) — a 3 s confirm cannot elapse inside the
footage; the settle phase is what lets production T=3.0 be measured on
this dataset at all.

Metrics:
- falls: fired (fall_detected event), trigger-only (cascade entered
  CONFIRMING but never fired), missed (neither)
- ADL: false-positive events, split in-footage vs settle-only, per hour
  of footage

Usage (from edge/):
    .venv/bin/python tools/fall_benchmark.py \
        --dataset ../mlops/datasets/ur_fall [--confirm 3.0] [--settle 3.5] \
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

from mobisentra.analytics.fall import FallConfig, FallDetector
from mobisentra.vision.pose import PoseTracker
from mobisentra.vision.track_history import TrackHistory
from mobisentra.vision.tracker import TrackedPerson


@dataclass
class ClipResult:
    name: str
    split: str
    duration_s: float
    fired: int = 0
    triggered: int = 0
    events: list[dict] = field(default_factory=list)


class CascadeBench:
    """One clip → CascadeBench.run(): single pass + settle."""

    def __init__(self, detector: FallDetector, tracker: PoseTracker) -> None:
        self.detector = detector
        self.tracker = tracker

    def _step(self, history: TrackHistory, now_ts: float) -> list[dict]:
        pose_map = {tid: history.pose_history(tid) for tid in history.track_ids()}
        events: list[dict] = []
        for track_id, samples in pose_map.items():
            others = {o: s for o, s in pose_map.items() if o != track_id}
            for candidate in self.detector.update(track_id, samples, now_ts=now_ts, others=others):
                events.append(
                    {
                        "track_id": candidate.track_id,
                        "trigger_ts": candidate.trigger_ts,
                        "ts": candidate.ts,
                        "confidence": candidate.confidence,
                    }
                )
        return events

    def run(self, path: Path, settle_s: float, trace: bool) -> ClipResult:
        result = ClipResult(
            name=path.stem,
            split="falls" if "fall-" in path.stem else "adl",
            duration_s=0.0,
        )
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        history = TrackHistory()
        all_events: list[dict] = []
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ts = index / fps
            poses = self.tracker.process_frame(frame)
            people = [
                TrackedPerson(track_id=p.track_id, bbox=p.bbox, confidence=p.confidence)
                for p in poses
            ]
            history.update(ts, people)
            history.update_poses(ts, poses)
            all_events.extend(self._step(history, ts))
            if trace and index % 15 == 0:
                pending = self.detector.pending_track_ids()
                if pending or poses:
                    print(f"  t={ts:5.2f} tracks={[p.track_id for p in poses]} pending={pending}")
            index += 1
        cap.release()
        result.duration_s = index / fps

        for step in range(1, int(settle_s / 0.1) + 1):
            all_events.extend(self._step(history, result.duration_s + step * 0.1))

        result.fired = len(all_events)
        result.triggered = len(self.detector.pending_track_ids())
        result.events = all_events
        return result


def load_clips(dataset: Path) -> list[Path]:
    clips = sorted(dataset.glob("fall-*-cam0.mp4")) + sorted(dataset.glob("adl-*-cam0.mp4"))
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
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--trace", default="")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    clips = load_clips(args.dataset)
    if args.limit:
        clips = clips[: args.limit]
    config = FallConfig(
        confirm_seconds=args.confirm,
        velocity_body_heights_per_s=args.velocity,
        velocity_high_bar_bh_s=args.velocity_high,
    )
    detector = FallDetector(config)
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
        detector = FallDetector(config)
        bench = CascadeBench(detector, tracker)
        result = bench.run(clip, args.settle, trace=args.trace in clip.stem)
        results.append(result)
        flag = "FIRE" if result.fired else ("trig" if result.triggered else "----")
        print(
            f"[{flag}] {result.name:<18} {result.duration_s:5.1f}s "
            f"events={result.fired} pending={result.triggered}"
        )

    falls = [r for r in results if r.split == "falls"]
    adls = [r for r in results if r.split == "adl"]
    fired = sum(1 for r in falls if r.fired)
    triggered_only = sum(1 for r in falls if r.triggered and not r.fired)
    missed = len(falls) - fired - triggered_only
    fp_footage = sum(1 for r in adls for e in r.events if e["ts"] <= r.duration_s + 1e-6)
    fp_settle = sum(1 for r in adls for e in r.events if e["ts"] > r.duration_s + 1e-6)
    footage_hours = sum(r.duration_s for r in adls) / 3600.0

    print(f"\n=== UR Fall benchmark (confirm={args.confirm}s, settle={args.settle}s) ===")
    print(
        f"falls: {len(falls)}  fired={fired} ({100 * fired / max(1, len(falls)):.0f}%)  "
        f"trigger-only={triggered_only}  missed={missed}"
    )
    print(
        f"adl:   {len(adls)}  fp_in_footage={fp_footage}  fp_settle_only={fp_settle}  "
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
            "model": args.model,
            "falls": {
                "total": len(falls),
                "fired": fired,
                "trigger_only": triggered_only,
                "missed": missed,
            },
            "adl": {
                "total": len(adls),
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
