#!/usr/bin/env python3
"""Negatives FP soak for the fight path (Phase 5, Step 5.4).

Runs every clip in the negatives corpus through the FULL production fight
stack — yolo26n-pose tracking → PairFinder → per-pair ActionScorer (real
ONNX) → FightDetector fusion — and reports every ``altercation_suspected``
row. The corpus is normal interaction only (hugging, playing, rushing,
assisting, crowds), so the expected alert count is ZERO; any firing row is
a false positive to triage. Exit code 1 when alerts fire (CI-able).

Usage (from repo root or edge/):
    cd edge && uv run python tools/fight_negatives_soak.py \
        --onnx ../mlops/datasets/movinet/movinet_a2_explicit_states.onnx
        [--negatives sample_data/negatives] [--out runs/fight-negatives-soak.json]
        [--analysis-fps 5] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2

from mobisentra.analytics.engine import CameraAnalytics
from mobisentra.ingestion.config import CameraConfig
from mobisentra.vision.action import ActionScorer
from mobisentra.vision.pose import PoseTracker


def load_config() -> tuple[str, str, str, int, int]:
    import yaml

    cfg = yaml.safe_load(Path("configs/detection.yaml").read_text())
    return (
        str(cfg["model"]),
        str(cfg.get("tracker", "bytetrack.yaml")),
        str(cfg.get("device", "auto")),
        int(cfg.get("conf", 0.3)),
        int(cfg.get("imgsz", 640)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--negatives", type=Path, default=Path("sample_data/negatives"))
    parser.add_argument("--out", type=Path, default=Path("runs/fight-negatives-soak.json"))
    parser.add_argument("--analysis-fps", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    clips = sorted(args.negatives.glob("neg_*.mp4"))
    if args.limit:
        clips = clips[: args.limit]
    if not clips:
        raise SystemExit(f"no clips matching {args.negatives}/neg_*.mp4")

    model, tracker_cfg, device, conf, imgsz = load_config()
    tracker = PoseTracker(
        model=model, conf=conf, classes=[0], imgsz=imgsz, tracker=tracker_cfg, device=device
    )

    per_clip = []
    total_alerts = 0
    total_frames = 0
    total_s = 0.0
    t_start = time.time()
    for clip in clips:
        def factory(onnx_path=args.onnx):
            return ActionScorer(onnx_path)

        analytics = CameraAnalytics(
            CameraConfig(id=clip.stem, source=f"file://{clip}", vehicle_id="NEG", zones={}),
            action_scorer_factory=factory,
        )
        cap = cv2.VideoCapture(str(clip))
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(src_fps / args.analysis_fps)))
        rows = []
        frame_i = analyzed = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_i += 1
            if frame_i % step:
                continue
            analyzed += 1
            people = tracker.process_frame(frame)
            rows.extend(analytics.process(frame_i / src_fps, frame, people))
        cap.release()
        duration_s = frame_i / src_fps
        alerts = [row for row in rows if row["kind"] == "altercation_suspected"]
        total_alerts += len(alerts)
        total_frames += analyzed
        total_s += duration_s
        per_clip.append(
            {
                "clip": clip.name,
                "duration_s": round(duration_s, 2),
                "analyzed_frames": analyzed,
                "alerts": len(alerts),
                "alert_rows": alerts,
            }
        )
        flag = "ALERT" if alerts else "ok"
        print(f"[{flag}] {clip.name} {duration_s:.0f}s {analyzed}fr alerts={len(alerts)}")

    report = {
        "date": time.strftime("%Y-%m-%d"),
        "onnx": str(args.onnx),
        "corpus": str(args.negatives),
        "clips": len(clips),
        "corpus_minutes": round(total_s / 60.0, 2),
        "analyzed_frames": total_frames,
        "wall_s": round(time.time() - t_start, 1),
        "total_alerts": total_alerts,
        "per_clip": per_clip,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(
        f"[done] {len(clips)} clips / {total_s / 60:.1f} min corpus -> {total_alerts} alerts "
        f"({report['wall_s']}s wall) -> {args.out}"
    )
    return 1 if total_alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
