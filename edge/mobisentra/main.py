"""Phase 1 pipeline orchestrator: N cameras → consumer loops → metrics.

Runs every camera in the registry through a StreamReader, consumes the
latest frames (honoring ``analyze_every_n_frames``), records per-minute
JSONL metrics, and prints an end-of-run summary.

Usage:
  uv run python -m mobisentra.main --config configs/cameras.yaml \
      [--minutes 10] [--preview] [--rtsp rtsp://host/stream]
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mobisentra.ingestion.config import CameraConfig, load_cameras
from mobisentra.ingestion.sources import open_real_capture, resolve_source
from mobisentra.ingestion.stream_reader import RealClock, StreamReader
from mobisentra.metrics import MetricsWriter, MinuteStats, percentile

MINUTE_S = 60.0


@dataclass
class CameraAccumulator:
    camera: CameraConfig
    reader: StreamReader
    lags_ms: list[float] = field(default_factory=list)
    consumed: int = 0
    last_stats: StreamStatsSnapshot | None = None
    detector: object | None = None
    history: object | None = None
    debug_sink: object | None = None


@dataclass
class StreamStatsSnapshot:
    frames_read: int
    frames_fetched: int
    reconnects: int
    state: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MobiSentra ingestion runner")
    parser.add_argument("--config", type=Path, default=Path("configs/cameras.yaml"))
    parser.add_argument("--minutes", type=float, default=0.0,
                        help="stop after N minutes (0 = run until Ctrl-C)")
    parser.add_argument("--preview", action="store_true", help="show video windows")
    parser.add_argument("--rtsp", action="append", default=[],
                        help="extra RTSP source URL (camera id RTSP_EXTRA_n)")
    parser.add_argument("--metrics", type=Path, default=None,
                        help="metrics JSONL path (default runs/ingestion-<ts>.jsonl)")
    parser.add_argument("--detect", action="store_true",
                        help="run person detection + tracking (Phase 2)")
    parser.add_argument("--detection-config", type=Path,
                        default=Path("configs/detection.yaml"))
    parser.add_argument("--debug-detections", action="store_true",
                        help="write per-frame detection JSONL to runs/debug/")
    return parser.parse_args(argv)


def build_cameras(args: argparse.Namespace) -> list[CameraConfig]:
    cameras = list(load_cameras(args.config))
    for i, url in enumerate(args.rtsp, start=1):
        cameras.append(CameraConfig(id=f"RTSP_EXTRA_{i}", source=url,
                                    vehicle_id="RTSP", analyze_every_n_frames=1))
    return cameras


def load_detection_config(path: Path) -> dict:
    import yaml

    if not path.is_file():
        raise SystemExit(f"detection config not found: {path}")
    return yaml.safe_load(path.read_text()) or {}


def attach_detection(accs: list[CameraAccumulator], det_cfg: dict, debug: bool) -> None:
    from mobisentra.vision.track_history import TrackHistory
    from mobisentra.vision.tracker import DetectorTracker, resolve_device

    print(
        f"[main] detection: model={det_cfg.get('model')} "
        f"device={resolve_device(det_cfg.get('device', 'auto'))}"
    )
    debug_dir = Path("runs/debug") if debug else None
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
    for acc in accs:
        acc.detector = DetectorTracker(**det_cfg)
        acc.history = TrackHistory()
        if debug_dir is not None:
            acc.debug_sink = (debug_dir / f"{acc.camera.id}.jsonl").open("w")


def run_frame(acc: CameraAccumulator, frame, detect: bool, draw_on: np.ndarray | None) -> None:
    if not detect or acc.detector is None:
        return
    people = acc.detector.process_frame(frame.image)
    acc.history.update(frame.capture_ts, people)
    if acc.debug_sink is not None:
        import json

        payload = {
            "ts": frame.capture_ts,
            "frame_index": frame.frame_index,
            "people": [
                {"id": p.track_id, "bbox": list(p.bbox), "conf": p.confidence}
                for p in people
            ],
        }
        acc.debug_sink.write(json.dumps(payload) + "\n")
    if draw_on is not None:
        import cv2

        for p in people:
            x1, y1, x2, y2 = (int(v) for v in p.bbox)
            cv2.rectangle(draw_on, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(draw_on, f"ID {p.track_id}", (x1, max(12, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    clock = RealClock()

    cameras = build_cameras(args)
    metrics_path = args.metrics or Path(
        f"runs/ingestion-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    )
    writer = MetricsWriter(metrics_path)
    print(f"[main] {len(cameras)} camera(s); metrics -> {metrics_path}")

    accs: list[CameraAccumulator] = []
    for camera in cameras:
        spec = resolve_source(camera.source)
        reader = StreamReader(camera.id, spec, open_capture=open_real_capture)
        reader.start()
        accs.append(CameraAccumulator(camera=camera, reader=reader))
        print(f"[main] started {camera.id} ({spec.kind}: {spec.capture_arg})")

    if args.detect:
        attach_detection(
            accs,
            load_detection_config(args.detection_config),
            debug=args.debug_detections,
        )

    stopped = {"flag": False}

    def request_stop(signum, frame):
        stopped["flag"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    started = clock.monotonic()
    next_minute = started + MINUTE_S
    deadline = started + args.minutes * MINUTE_S if args.minutes > 0 else None

    try:
        while not stopped["flag"]:
            now = clock.monotonic()
            if deadline is not None and now >= deadline:
                break

            for acc in accs:
                timeout = 0.05 if len(accs) == 1 else 0.01
                frame = acc.reader.get_frame(timeout_s=timeout)
                if frame is None:
                    continue
                if acc.camera.analyze_every_n_frames > 1:
                    if frame.frame_index % acc.camera.analyze_every_n_frames != 0:
                        continue
                acc.consumed += 1
                acc.lags_ms.append(max(0.0, (clock.time() - frame.capture_ts) * 1000.0))
                run_frame(acc, frame, detect=args.detect,
                          draw_on=frame.image if args.preview else None)
                if args.preview:
                    import cv2

                    cv2.imshow(acc.camera.id, frame.image)
            if args.preview:
                import cv2

                cv2.waitKey(1)

            if clock.monotonic() >= next_minute:
                elapsed = clock.monotonic() - (next_minute - MINUTE_S)
                for acc in accs:
                    stats = acc.reader.status()
                    snapshot = StreamStatsSnapshot(
                        frames_read=stats.frames_read,
                        frames_fetched=stats.frames_fetched,
                        reconnects=stats.reconnects,
                        state=stats.state,
                    )
                    prev = acc.last_stats
                    read_delta = snapshot.frames_read - (prev.frames_read if prev else 0)
                    consumed_delta = snapshot.frames_fetched - (
                        prev.frames_fetched if prev else 0
                    )
                    minute = MinuteStats(
                        camera_id=acc.camera.id,
                        ts=clock.time(),
                        read_fps=read_delta / elapsed,
                        consumed_fps=consumed_delta / elapsed,
                        lag_p50_ms=percentile(acc.lags_ms, 0.50),
                        lag_p95_ms=percentile(acc.lags_ms, 0.95),
                        lag_max_ms=max(acc.lags_ms, default=0.0),
                        dropped=snapshot.frames_read - snapshot.frames_fetched,
                        reconnects=snapshot.reconnects,
                    )
                    writer.write(minute)
                    print(
                        f"[main] {minute.camera_id}: state={snapshot.state} "
                        f"read={minute.read_fps:.1f}fps consumed={minute.consumed_fps:.1f}fps "
                        f"lag p50/p95/max={minute.lag_p50_ms:.0f}/"
                        f"{minute.lag_p95_ms:.0f}/{minute.lag_max_ms:.0f}ms "
                        f"reconnects={minute.reconnects}"
                    )
                    acc.lags_ms.clear()
                    acc.last_stats = snapshot
                next_minute += MINUTE_S
    finally:
        print("[main] stopping readers…")
        for acc in accs:
            acc.reader.stop()
            if acc.debug_sink is not None:
                acc.debug_sink.close()
        if args.preview:
            import cv2

            cv2.destroyAllWindows()

    print_summary(accs, clock.monotonic() - started)
    return 0


def print_summary(accs: list[CameraAccumulator], uptime_s: float) -> None:
    print(f"\n[summary] uptime {uptime_s:.0f}s")
    header = f"{'camera':<18}{'state':<14}{'read':>9}{'fetched':>9}{'reconn':>8}"
    print(header)
    for acc in accs:
        stats = acc.reader.status()
        print(
            f"{acc.camera.id:<18}{stats.state:<14}{stats.frames_read:>9}"
            f"{stats.frames_fetched:>9}{stats.reconnects:>8}"
        )


if __name__ == "__main__":
    sys.exit(run())
