"""Per-frame pipeline pieces (Phase 3 wiring; event engine since Phase 6).

Everything the run loop calls per camera: accumulator state, attach steps
(detection, analytics, event engine), single-frame processing (detect →
track → zone analytics → raw rows + debounced CloudEvents envelopes →
overlays), and the per-minute metrics rollup. ``main.py`` owns CLI +
lifecycle; this module owns frame handling and composition.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from mobisentra.ingestion.config import CameraConfig
from mobisentra.ingestion.stream_reader import RealClock, StreamReader
from mobisentra.metrics import MinuteStats, percentile

if TYPE_CHECKING:
    from mobisentra.messaging.config import MessagingConfig


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
    event_engine: object | None = None
    envelope_sink: object | None = None
    publisher: object | None = None


@dataclass
class MessagingHandle:
    """Lifecycle handle for the edge-wide publisher (one spool, one MQTT
    client shared by every camera). ``shutdown`` stops the drain loop,
    attempts one final replay pass (transport still alive), then closes."""

    publisher: object
    transport: object

    def shutdown(self) -> None:
        self.publisher.stop()
        self.publisher.drain_once()
        self.transport.close()


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


def attach_messaging(
    accs: list[CameraAccumulator],
    config: MessagingConfig,
    *,
    transport_factory: Callable[[MessagingConfig], object] | None = None,
    start: bool = True,
) -> MessagingHandle:
    """Build the edge-wide EventPublisher (spool + transport) and attach the
    same instance to every camera. ``transport_factory`` overrides the paho
    adapter (tests); ``start=False`` keeps the drain loop off for
    deterministic manual draining."""
    from mobisentra.messaging.publisher import EventPublisher
    from mobisentra.messaging.spool import SpoolQueue
    from mobisentra.messaging.transport_paho import PahoTransport

    if transport_factory is None:

        def default_transport(cfg: MessagingConfig) -> PahoTransport:
            return PahoTransport(
                url=cfg.url, client_id=cfg.client_id, puback_timeout_s=cfg.puback_timeout_s
            )

        transport_factory = default_transport
    spool = SpoolQueue(config.spool_path, max_entries=config.spool_max_entries)
    transport = transport_factory(config)
    publisher = EventPublisher(
        spool=spool,
        transport=transport,
        topic=config.topic,
        batch=config.replay_batch,
        backoff_initial_s=config.backoff_initial_s,
        backoff_max_s=config.backoff_max_s,
    )
    for acc in accs:
        acc.publisher = publisher
    if start:
        publisher.start()
    if accs:
        stats = spool.stats()
        print(
            f"[main] messaging: {config.url} topic={config.topic} "
            f"spool={config.spool_path} (pending {stats.pending}/{stats.total})"
        )
    return MessagingHandle(publisher=publisher, transport=transport)


def build_model_versions(
    det_cfg: Mapping[str, object], action_onnx: Path | None = None
) -> dict[str, str]:
    """Stamp what actually runs (runbook 6.3): the detector/pose model from
    the detection config, plus the action ONNX as ``name@sha8`` when a fight
    path is wired (benchmarks wire it today; production wiring lands with
    the fight enablement). Config-declared versions would drift from
    reality — derive, don't declare."""
    versions = {"detector": str(det_cfg.get("model") or "unspecified")}
    if action_onnx is not None:
        if not action_onnx.is_file():
            raise ValueError(f"action model not found: {action_onnx}")
        digest = hashlib.sha256(action_onnx.read_bytes()).hexdigest()[:8]
        versions["action"] = f"{action_onnx.stem}@{digest}"
    return versions


def attach_analytics(
    accs: list[CameraAccumulator],
    det_cfg: dict,
    severity_path: Path = Path("configs/severity.yaml"),
) -> None:
    from mobisentra.analytics.engine import CameraAnalytics
    from mobisentra.events.engine import EventEngine
    from mobisentra.events.envelope import EnvelopeBuilder
    from mobisentra.events.severity import SeverityConfigError, load_severity_policy, make_resolver
    from mobisentra.events.sink import JsonlEventWriter

    if not severity_path.is_file():
        raise SystemExit(f"severity config not found: {severity_path}")
    try:
        policy = load_severity_policy(severity_path)
    except SeverityConfigError as exc:
        raise SystemExit(str(exc)) from exc
    model_versions = build_model_versions(det_cfg)
    events_dir = Path("runs/events")
    evidence_dir = Path("runs/evidence")
    for acc in accs:
        acc.analytics = CameraAnalytics(acc.camera, history=acc.history, evidence_root=evidence_dir)
        acc.event_sink = JsonlEventWriter(events_dir / f"{acc.camera.id}.jsonl")
        acc.event_engine = EventEngine(
            builder=EnvelopeBuilder(
                source=f"/mobisentra/edge/{acc.camera.vehicle_id}/{acc.camera.id}",
                model_versions=model_versions,
            ),
            policy=policy.debounce(),
            resolver=make_resolver(policy),
        )
        acc.envelope_sink = JsonlEventWriter(events_dir / f"{acc.camera.id}.envelopes.jsonl")
    if accs:
        summary = ", ".join(f"{acc.camera.id} ({len(acc.camera.zones)} zone(s))" for acc in accs)
        print(
            f"[main] analytics: {summary}; rows -> {events_dir}/<camera_id>.jsonl; "
            f"envelopes -> {events_dir}/<camera_id>.envelopes.jsonl; "
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
        if acc.event_engine is not None:
            envelopes = acc.event_engine.process(event_rows)
            if acc.envelope_sink is not None:
                for envelope in envelopes:
                    acc.envelope_sink.write(envelope)
            if acc.publisher is not None:
                for envelope in envelopes:
                    acc.publisher.publish(envelope)
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
