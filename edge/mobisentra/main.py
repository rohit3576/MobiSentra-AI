"""Pipeline orchestrator: N cameras → consumer loops → metrics (Phase 1+).

Runs every camera in the registry through a StreamReader, consumes the
latest frames (honoring ``analyze_every_n_frames``), records per-minute
JSONL metrics, and prints an end-of-run summary. With ``--detect``, Phase 2
detection/tracking and Phase 3 zone analytics run per frame; zone events
land in ``runs/events/<camera_id>.jsonl``.

Usage:
  uv run python -m mobisentra.main --config configs/cameras.yaml \
      [--minutes 10] [--preview] [--detect] [--rtsp rtsp://host/stream]
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from mobisentra.ingestion.config import CameraConfig, load_cameras
from mobisentra.ingestion.sources import open_real_capture, resolve_source
from mobisentra.ingestion.stream_reader import RealClock, StreamReader
from mobisentra.metrics import MetricsWriter
from mobisentra.pipeline import (
    CameraAccumulator,
    attach_analytics,
    attach_detection,
    rollup_minute,
    run_frame,
)

MINUTE_S = 60.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MobiSentra ingestion runner")
    parser.add_argument("--config", type=Path, default=Path("configs/cameras.yaml"))
    parser.add_argument(
        "--minutes", type=float, default=0.0, help="stop after N minutes (0 = run until Ctrl-C)"
    )
    parser.add_argument("--preview", action="store_true", help="show video windows")
    parser.add_argument(
        "--rtsp", action="append", default=[], help="extra RTSP source URL (camera id RTSP_EXTRA_n)"
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        default=None,
        help="metrics JSONL path (default runs/ingestion-<ts>.jsonl)",
    )
    parser.add_argument(
        "--detect", action="store_true", help="run person detection + tracking (Phase 2)"
    )
    parser.add_argument("--detection-config", type=Path, default=Path("configs/detection.yaml"))
    parser.add_argument(
        "--debug-detections",
        action="store_true",
        help="write per-frame detection JSONL to runs/debug/",
    )
    return parser.parse_args(argv)


def build_cameras(args: argparse.Namespace) -> list[CameraConfig]:
    cameras = list(load_cameras(args.config))
    for i, url in enumerate(args.rtsp, start=1):
        cameras.append(
            CameraConfig(
                id=f"RTSP_EXTRA_{i}", source=url, vehicle_id="RTSP", analyze_every_n_frames=1
            )
        )
    return cameras


def load_detection_config(path: Path) -> dict:
    import yaml

    if not path.is_file():
        raise SystemExit(f"detection config not found: {path}")
    return yaml.safe_load(path.read_text()) or {}


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    clock = RealClock()

    cameras = build_cameras(args)
    metrics_path = args.metrics or Path(f"runs/ingestion-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
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
        attach_analytics(accs)

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
                run_frame(
                    acc, frame, detect=args.detect, draw_on=frame.image if args.preview else None
                )
                if args.preview:
                    import cv2

                    cv2.imshow(acc.camera.id, frame.image)
            if args.preview:
                import cv2

                cv2.waitKey(1)

            if clock.monotonic() >= next_minute:
                elapsed = clock.monotonic() - (next_minute - MINUTE_S)
                for acc in accs:
                    minute, snapshot = rollup_minute(acc, clock, elapsed)
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
            if acc.event_sink is not None:
                acc.event_sink.close()
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
