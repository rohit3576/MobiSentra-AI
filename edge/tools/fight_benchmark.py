#!/usr/bin/env python3
"""Gate-5 fight benchmark (Phase 5, Step 5.5a).

Three protocols over the production fight stack (``CameraAnalytics`` fight
path on ``PoseTracker``):

- **UBI-Fights (PRIMARY, frame-GT windows)**: a fight clip counts detected
  when an ``altercation_suspected`` alert lands inside a GT fight window
  (±1 s lead/lag); normal clips contribute FP events and FP/hr. No settle
  phase — the 0.6 s fusion hold fits inside multi-second fight segments
  (the UR Fall tight-clip lesson does not apply here).
- **Hockey (trigger-stage ONLY)**: 2 s clips cannot host the full
  sustain+hold chain at 5 fps analysis — reported metrics are pair
  formation + peak action score, documented as trigger-stage, not alerts.
- **Negatives (FP regression)**: the Step 5.4 corpus — expects 0 alerts.

``--selftest`` runs the whole machinery on synthetic clips with a stub
tracker + stub scorer (no models, no datasets — CI-able smoke of the
runner itself).

Usage (from edge/):
    uv run python tools/fight_benchmark.py --ubi ../mlops/datasets/ubi_fights \\
        [--hockey ../mlops/datasets/hockey] [--negatives sample_data/negatives] \\
        --onnx ../mlops/datasets/movinet/movinet_a2_explicit_states.onnx \\
        [--action-min 0.6] [--rel-motion 0.6] [--analysis-fps 5] [--limit N] \\
        [--json runs/fight-benchmark.json] [--selftest]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from mobisentra.analytics.engine import CameraAnalytics
from mobisentra.analytics.fight import FightConfig
from mobisentra.analytics.pairs import PairConfig
from mobisentra.ingestion.config import CameraConfig
from mobisentra.vision.action import ActionScore, shared_session_factory
from mobisentra.vision.pose import PoseTracker
from mobisentra.vision.tracker import TrackedPerson

FIGHT_EVENT = "altercation_suspected"
WINDOW_LEAD_S = 1.0

# Windowed protocol (owner directive 2026-08-28: no long-video testing) —
# fight clips analyze only [first window - 5 s, last window + 2 s] capped at
# 120 s (pair formation + scorer warm-up need the lead; the hold chain needs
# the lag); normal clips analyze a 60 s prefix. Denominators use ANALYZED
# time. Full-length salvage comparison: runs/fight-benchmark-salvage.json.
GT_LEAD_S = 5.0
GT_LAG_S = 2.0
MAX_ANALYZE_S = 120.0
NORMAL_CAP_S = 60.0


@dataclass
class ClipOutcome:
    name: str
    is_fight: bool
    duration_s: float
    alerts: int = 0
    alert_ts: list[float] = field(default_factory=list)
    detected: bool = False
    pair_formed: bool = False
    peak_score: float = 0.0


def load_detection_config() -> dict:
    import yaml

    return yaml.safe_load(Path("configs/detection.yaml").read_text())


def make_tracker(cfg: dict) -> PoseTracker:
    return PoseTracker(
        model=str(cfg["model"]),
        conf=float(cfg.get("conf", 0.3)),
        classes=[0],
        imgsz=int(cfg.get("imgsz", 640)),
        tracker=str(cfg.get("tracker", "bytetrack.yaml")),
        device=str(cfg.get("device", "auto")),
    )


def onnx_factory(onnx_path: Path):
    return shared_session_factory(onnx_path)


class StubScorer:
    """Deterministic model-free scorer for --selftest: score scales with the
    crop's share of frame width (wider union box = more/bigger subjects)."""

    def __init__(self, frame_width: int = 640) -> None:
        self._frame_width = frame_width

    def score(self, crop_bgr: np.ndarray) -> ActionScore:
        width = max(1, crop_bgr.shape[1])
        fight = min(1.0, 0.55 + 0.45 * (width / self._frame_width))
        return ActionScore(fight=fight, no_fight=1.0 - fight, logit_fight=float(fight))


class StubTracker:
    """Scripted tracker for --selftest: emits scripted TrackedPerson lists."""

    produces_pose = True

    def __init__(self, script: list[list[TrackedPerson]]) -> None:
        self._script = script
        self._i = 0

    def process_frame(self, image: np.ndarray) -> list[TrackedPerson]:
        people = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return people


def parse_ubi_gt(path: Path) -> list[tuple[int, int]]:
    """UBI annotation CSV: one label per line (line N = frame N); the
    two-column `frame;label` form is also accepted for robustness."""
    rows: list[tuple[int, int]] = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        tokens = re.split(r"[;,\s]+", line.strip())
        if not tokens or not tokens[0]:
            continue
        if len(tokens) == 1:
            if tokens[0].isdigit():
                rows.append((lineno, int(tokens[0])))
            continue
        if tokens[0].isdigit():
            rows.append((int(tokens[0]), int(tokens[-1])))
    return rows


def fight_windows(gt_rows: list[tuple[int, int]], fps: float) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    start: int | None = None
    prev = 0
    for frame, label in gt_rows:
        if label == 1 and prev == 0:
            start = frame
        if label == 0 and prev == 1 and start is not None:
            windows.append((start / fps, frame / fps))
            start = None
        prev = label
    if start is not None:
        windows.append((start / fps, (gt_rows[-1][0] if gt_rows else start) / fps))
    return windows


def run_clip(
    clip: Path,
    tracker,
    factory,
    fight_config: FightConfig,
    pair_config: PairConfig,
    analysis_fps: float,
    start_s: float = 0.0,
    max_seconds: float | None = None,
) -> ClipOutcome:
    cap = cv2.VideoCapture(str(clip))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / analysis_fps)))
    seek_frame = int(round(start_s * src_fps))
    if seek_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, seek_frame)
    analytics = CameraAnalytics(
        CameraConfig(id=clip.stem, source=f"file://{clip}", vehicle_id="BENCH", zones={}),
        fight_config=fight_config,
        pair_config=pair_config,
        action_scorer_factory=factory,
    )
    outcome = ClipOutcome(name=clip.name, is_fight=False, duration_s=0.0)
    print(f"[..] {clip.name}" + (f" @{start_s:.0f}s" if start_s else ""))
    frame_i = 0
    end_s = None if max_seconds is None else start_s + max_seconds
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_i += 1
        absolute = seek_frame + frame_i
        ts = absolute / src_fps
        if end_s is not None and ts > end_s:
            break
        if absolute % step:
            continue
        people = tracker.process_frame(frame)
        for row in analytics.process(ts, frame, people):
            if row["kind"] == FIGHT_EVENT:
                outcome.alerts += 1
                outcome.alert_ts.append(row["ts"])
        if analytics.last_action_scores:
            outcome.pair_formed = True
            outcome.peak_score = max(outcome.peak_score, max(analytics.last_action_scores.values()))
    cap.release()
    outcome.duration_s = (frame_i / src_fps) if frame_i else 0.0
    return outcome


def bench_ubi(
    root: Path, tracker, factory, fight_config, pair_config, analysis_fps, limit: int
) -> dict:
    videos = sorted((root / "videos").rglob("*.mp4")) + sorted((root / "videos").rglob("*.avi"))
    annotations = root / "annotation"
    split_file = root / "test_videos.csv"
    if split_file.exists():
        test_names = {line.strip() for line in split_file.read_text().splitlines() if line.strip()}
        videos = [v for v in videos if v.stem in test_names]
    if limit:
        fights = [v for v in videos if v.name.startswith("F")]
        normals = [v for v in videos if v.name.startswith("N")]
        videos = fights[: max(1, limit // 2)] + normals[: max(1, limit // 2)]
    per_clip = []
    detected = fp_events = 0
    fight_clips = normal_clips = 0
    normal_minutes = 0.0
    for video in videos:
        gt_path = annotations / f"{video.stem}.csv"
        windows = fight_windows(parse_ubi_gt(gt_path), 30.0) if gt_path.exists() else []
        if windows:
            span_start = max(0.0, windows[0][0] - GT_LEAD_S)
            span_len = min(windows[-1][1] + GT_LAG_S - span_start, MAX_ANALYZE_S)
            outcome = run_clip(
                video, tracker, factory, fight_config, pair_config, analysis_fps,
                start_s=span_start, max_seconds=span_len,
            )
        else:
            outcome = run_clip(
                video, tracker, factory, fight_config, pair_config, analysis_fps,
                max_seconds=NORMAL_CAP_S,
            )
        outcome.is_fight = video.name.startswith("F") or bool(windows)
        if outcome.is_fight:
            fight_clips += 1
            outcome.detected = any(
                any(start - WINDOW_LEAD_S <= ts <= end + WINDOW_LEAD_S for start, end in windows)
                for ts in outcome.alert_ts
            ) or (not windows and outcome.alerts > 0)
            detected += int(outcome.detected)
        else:
            normal_clips += 1
            normal_minutes += outcome.duration_s / 60.0
            fp_events += outcome.alerts
        per_clip.append(
            {
                "clip": outcome.name,
                "fight": outcome.is_fight,
                "detected": outcome.detected,
                "alerts": outcome.alerts,
                "analyzed_s": round(outcome.duration_s, 2),
                "windows": [(round(a, 2), round(b, 2)) for a, b in windows],
            }
        )
        flag = "HIT" if outcome.detected else ("ALERT" if outcome.alerts else "ok")
        print(f"[{flag}] {outcome.name} {outcome.duration_s:.0f}s alerts={outcome.alerts}")
    return {
        "protocol": "ubi-fights",
        "fight_clips": fight_clips,
        "detected": detected,
        "detection_rate": round(detected / fight_clips, 4) if fight_clips else None,
        "normal_clips": normal_clips,
        "normal_minutes": round(normal_minutes, 2),
        "fp_events": fp_events,
        "fp_per_hour": round(fp_events / normal_minutes * 60.0, 3) if normal_minutes else None,
        "per_clip": per_clip,
    }


def bench_hockey(
    root: Path,
    tracker,
    factory,
    fight_config,
    pair_config,
    analysis_fps,
    limit: int,
    action_min: float,
) -> dict:
    files = sorted(p for p in root.rglob("*.mp4")) + sorted(p for p in root.rglob("*.avi"))
    if limit:
        files = files[:limit]
    per_clip = []
    for video in files:
        outcome = run_clip(video, tracker, factory, fight_config, pair_config, analysis_fps)
        outcome.is_fight = bool(re.search(r"(^|/)(fi|fight)", video.name.lower()))
        per_clip.append(
            {
                "clip": outcome.name,
                "fight": outcome.is_fight,
                "pair_formed": outcome.pair_formed,
                "peak_score": round(outcome.peak_score, 3),
                "trigger_detected": outcome.pair_formed and outcome.peak_score >= action_min,
            }
        )
    fights = [c for c in per_clip if c["fight"]]
    normals = [c for c in per_clip if not c["fight"]]
    print(f"[hockey] {len(files)} clips ({len(fights)} fight / {len(normals)} non-fight)")

    def rate(group: list[dict], key: str) -> float | None:
        return round(sum(c[key] for c in group) / len(group), 4) if group else None

    return {
        "protocol": "hockey-trigger-stage",
        "note": "2 s clips: pair formation + peak action score only — the "
        "sustain+hold alert chain cannot fit; NOT alert-level metrics",
        "clips": len(files),
        "fight_pair_formed_rate": rate(fights, "pair_formed"),
        "fight_trigger_rate": rate(fights, "trigger_detected"),
        "nonfight_trigger_rate": rate(normals, "trigger_detected"),
        "per_clip": per_clip,
    }


def bench_negatives(
    root: Path, tracker, factory, fight_config, pair_config, analysis_fps, limit: int
) -> dict:
    clips = sorted(root.glob("neg_*.mp4"))
    if limit:
        clips = clips[:limit]
    alerts = 0
    minutes = 0.0
    for clip in clips:
        outcome = run_clip(clip, tracker, factory, fight_config, pair_config, analysis_fps)
        alerts += outcome.alerts
        minutes += outcome.duration_s / 60.0
        if outcome.alerts:
            print(f"[ALERT] {clip.name} alerts={outcome.alerts}")
    return {
        "protocol": "negatives-fp",
        "clips": len(clips),
        "corpus_minutes": round(minutes, 2),
        "alerts": alerts,
        "alerts_per_hour": round(alerts / minutes * 60.0, 3) if minutes else None,
    }


def selftest() -> int:
    """Synthetic clips + stub tracker + stub scorer end-to-end."""
    tmp = Path(tempfile.mkdtemp(prefix="fight-selftest-"))
    print(f"[selftest] workspace {tmp}")

    def make_video(name: str, frames: int, painter) -> Path:
        path = tmp / name
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (640, 360))
        for i in range(frames):
            frame = np.zeros((360, 640, 3), dtype=np.uint8)
            painter(frame, i)
            writer.write(frame)
        writer.release()
        return path

    def fight_painter(frame: np.ndarray, i: int) -> None:
        x = 200 + (15 if i % 2 == 0 else -15) * (1 if i % 4 < 2 else -1)
        cv2.rectangle(frame, (x, 100), (x + 80, 260), (60, 60, 60), -1)
        cv2.rectangle(frame, (x + 40, 100), (x + 130, 260), (90, 90, 90), -1)

    def calm_painter(frame: np.ndarray, i: int) -> None:
        cv2.rectangle(frame, (80, 100), (160, 260), (60, 60, 60), -1)
        cv2.rectangle(frame, (400, 100), (480, 260), (90, 90, 90), -1)

    fight_clip = make_video("F_selftest.mp4", 100, fight_painter)
    calm_clip = make_video("N_selftest.mp4", 100, calm_painter)

    def scripted_tracker(clip: Path):
        def painter_boxes(i: int) -> list[TrackedPerson]:
            if clip.name.startswith("F"):
                x = 200 + (15 if i % 2 == 0 else -15) * (1 if i % 4 < 2 else -1)
                overlap = 10 if (i // 5) % 2 == 0 else -70
                return [
                    TrackedPerson(
                        track_id=1, bbox=(float(x), 100.0, float(x + 80), 260.0), confidence=0.9
                    ),
                    TrackedPerson(
                        track_id=2,
                        bbox=(float(x + 80 + overlap), 100.0, float(x + 160 + overlap), 260.0),
                        confidence=0.9,
                    ),
                ]
            return [
                TrackedPerson(track_id=1, bbox=(80.0, 100.0, 160.0, 260.0), confidence=0.9),
                TrackedPerson(track_id=2, bbox=(400.0, 100.0, 480.0, 260.0), confidence=0.9),
            ]

        scripts = []
        for i in range(200):
            if i < 100:
                scripts.append(painter_boxes(i))
            else:
                scripts.append([])
        return StubTracker(scripts)

    fight_config = FightConfig(sustain_s=0.6)
    factory = lambda: StubScorer(640)  # noqa: E731 — benchmark-local stub

    pair_cfg = PairConfig()
    fight_outcome = run_clip(
        fight_clip, scripted_tracker(fight_clip), factory, fight_config, pair_cfg, 5.0
    )
    calm_outcome = run_clip(
        calm_clip, scripted_tracker(calm_clip), factory, fight_config, pair_cfg, 5.0
    )
    metrics = {
        "selftest": True,
        "fight_clip": {
            "alerts": fight_outcome.alerts,
            "pair_formed": fight_outcome.pair_formed,
            "peak_score": round(fight_outcome.peak_score, 3),
        },
        "calm_clip": {"alerts": calm_outcome.alerts, "pair_formed": calm_outcome.pair_formed},
        "stub_scorer": True,
    }
    ok = fight_outcome.alerts >= 1 and calm_outcome.alerts == 0
    print(json.dumps(metrics, indent=2))
    print(f"[selftest] {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ubi", type=Path)
    parser.add_argument("--hockey", type=Path)
    parser.add_argument("--negatives", type=Path)
    parser.add_argument("--onnx", type=Path)
    parser.add_argument("--analysis-fps", type=float, default=5.0)
    parser.add_argument("--action-min", type=float, default=0.6)
    parser.add_argument("--rel-motion", type=float, default=0.6)
    parser.add_argument("--sustain-s", type=float, default=0.6)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", type=Path, default=Path("runs/fight-benchmark.json"))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if not (args.ubi or args.hockey or args.negatives):
        parser.error("one of --ubi/--hockey/--negatives (or --selftest) is required")

    cfg = load_detection_config()
    tracker = make_tracker(cfg)
    if args.onnx is None:
        parser.error("--onnx is required outside --selftest")
    factory = onnx_factory(args.onnx)
    fight_config = FightConfig(
        action_score_min=args.action_min,
        rel_motion_diags_per_s=args.rel_motion,
        sustain_s=args.sustain_s,
    )

    report: dict = {"date": time.strftime("%Y-%m-%d")}
    if args.ubi:
        report["ubi"] = bench_ubi(
            args.ubi, tracker, factory, fight_config, PairConfig(), args.analysis_fps, args.limit
        )
    if args.hockey:
        report["hockey"] = bench_hockey(
            args.hockey,
            tracker,
            factory,
            fight_config,
            PairConfig(),
            args.analysis_fps,
            args.limit,
            args.action_min,
        )
    if args.negatives:
        report["negatives"] = bench_negatives(
            args.negatives, tracker, factory, fight_config, PairConfig(),
            args.analysis_fps, args.limit,
        )
    args.json.parent.mkdir(parents=True, exist_ok=True)
    report["config"] = {k: str(v) for k, v in vars(args).items()}
    args.json.write_text(json.dumps(report, indent=2))
    print(f"[done] -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
