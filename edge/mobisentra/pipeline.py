"""Per-frame pipeline pieces (Phase 3 wiring).

Everything the run loop calls per camera: accumulator state, attach steps
(detection, analytics), single-frame processing (detect → track → zone
analytics → event sink → overlays), and the per-minute metrics rollup.
``main.py`` owns CLI + lifecycle; this module owns frame handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mobisentra.ingestion.config import CameraConfig
from mobisentra.ingestion.stream_reader import RealClock, StreamReader
from mobisentra.metrics import MinuteStats, percentile


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
    analytics: object | None = None
    event_sink: object | None = None


@dataclass
class StreamStatsSnapshot:
    frames_read: int
    frames_fetched: int
    reconnects: int
    state: str


def attach_detection(accs: list[CameraAccumulator], det_cfg: dict, debug: bool) -> None:
    from mobisentra.vision.pose import PoseTracker
    from mobisentra.vision.track_history import TrackHistory
    from mobisentra.vision.tracker import DetectorTracker, resolve_device

    model = str(det_cfg.get("model", ""))
    detector_cls = PoseTracker if "-pose" in model else DetectorTracker
    print(
        f"[main] detection: model={model} "
        f"device={resolve_device(det_cfg.get('device', 'auto'))} "
        f"pose={'on' if detector_cls is PoseTracker else 'off'}"
    )
    debug_dir = Path("runs/debug") if debug else None
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
    for acc in accs:
        acc.detector = detector_cls(**det_cfg)
        acc.history = TrackHistory()
        if debug_dir is not None:
            acc.debug_sink = (debug_dir / f"{acc.camera.id}.jsonl").open("w")


def attach_analytics(accs: list[CameraAccumulator]) -> None:
    from mobisentra.analytics.engine import CameraAnalytics
    from mobisentra.events.sink import JsonlEventWriter

    events_dir = Path("runs/events")
    evidence_dir = Path("runs/evidence")
    for acc in accs:
        acc.analytics = CameraAnalytics(acc.camera, history=acc.history, evidence_root=evidence_dir)
        acc.event_sink = JsonlEventWriter(events_dir / f"{acc.camera.id}.jsonl")
    if accs:
        summary = ", ".join(f"{acc.camera.id} ({len(acc.camera.zones)} zone(s))" for acc in accs)
        print(
            f"[main] analytics: {summary}; events -> {events_dir}/<camera_id>.jsonl; "
            f"evidence -> {evidence_dir}/<camera_id>/"
        )


def run_frame(acc: CameraAccumulator, frame, detect: bool, draw_on: np.ndarray | None) -> None:
    if not detect or acc.detector is None:
        return
    from mobisentra.vision.pose import TrackedPose
    from mobisentra.vision.tracker import TrackedPerson

    people: list[TrackedPerson]
    if getattr(acc.detector, "produces_pose", False):
        poses: list[TrackedPose] = acc.detector.process_frame(frame.image)
        acc.history.update_poses(frame.capture_ts, poses)
        people = [
            TrackedPerson(track_id=p.track_id, bbox=p.bbox, confidence=p.confidence) for p in poses
        ]
    else:
        people = acc.detector.process_frame(frame.image)
    acc.history.update(frame.capture_ts, people)
    stale_ids = acc.history.purge(frame.capture_ts)
    if stale_ids and acc.analytics is not None:
        acc.analytics.forget(stale_ids)
    if acc.analytics is not None:
        event_rows = acc.analytics.process(frame.capture_ts, frame.image, people)
        if draw_on is not None:
            acc.analytics.draw_overlay(draw_on)
        if acc.event_sink is not None:
            for row in event_rows:
                acc.event_sink.write(row)
    if acc.debug_sink is not None:
        import json

        payload = {
            "ts": frame.capture_ts,
            "frame_index": frame.frame_index,
            "people": [
                {"id": p.track_id, "bbox": list(p.bbox), "conf": p.confidence} for p in people
            ],
        }
        acc.debug_sink.write(json.dumps(payload) + "\n")
    if draw_on is not None:
        import cv2

        for p in people:
            x1, y1, x2, y2 = (int(v) for v in p.bbox)
            cv2.rectangle(draw_on, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                draw_on,
                f"ID {p.track_id}",
                (x1, max(12, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )


def rollup_minute(
    acc: CameraAccumulator, clock: RealClock, elapsed_s: float
) -> tuple[MinuteStats, StreamStatsSnapshot]:
    stats = acc.reader.status()
    snapshot = StreamStatsSnapshot(
        frames_read=stats.frames_read,
        frames_fetched=stats.frames_fetched,
        reconnects=stats.reconnects,
        state=stats.state,
    )
    prev = acc.last_stats
    read_delta = snapshot.frames_read - (prev.frames_read if prev else 0)
    consumed_delta = snapshot.frames_fetched - (prev.frames_fetched if prev else 0)
    minute = MinuteStats(
        camera_id=acc.camera.id,
        ts=clock.time(),
        read_fps=read_delta / elapsed_s,
        consumed_fps=consumed_delta / elapsed_s,
        lag_p50_ms=percentile(acc.lags_ms, 0.50),
        lag_p95_ms=percentile(acc.lags_ms, 0.95),
        lag_max_ms=max(acc.lags_ms, default=0.0),
        dropped=snapshot.frames_read - snapshot.frames_fetched,
        reconnects=snapshot.reconnects,
    )
    return minute, snapshot
