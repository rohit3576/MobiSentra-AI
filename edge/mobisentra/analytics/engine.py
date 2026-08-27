"""Camera analytics composition (Phase 3 Day 4 + Phase 4 wiring).

Bundles the analytics pieces behind one per-camera object:
``ZoneEngine`` membership → ``OccupancyMonitor`` bands (emit
``occupancy_level_change`` on confirmed flips — the band a camera starts
in is not an event) + ``DwellTracker`` loiter/obstruction events, and —
when a track history is injected (pose model attached) — the ``FallDetector``
cascade with ``EvidenceBuffer``/``EvidenceWriter`` so every ``fall_detected``
row carries a playable ``evidence_ref`` clip. Tracks inside a REST zone
(beds/berths — lying expected) are excluded from the fall cascade: a
deliberate lie-down there is not a fall event. ``process`` returns
JSONL-ready rows; ``draw_overlay`` renders zone boundaries for
``--preview``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import cv2
import numpy as np

from mobisentra.analytics.fall import EVENT_KIND as FALL_EVENT_KIND
from mobisentra.analytics.fall import FallCandidate, FallConfig, FallDetector
from mobisentra.analytics.occupancy import OccupancyBand, OccupancyMonitor
from mobisentra.analytics.zone_events import DwellEventKind, DwellTracker
from mobisentra.analytics.zones import ZoneEngine
from mobisentra.events.evidence import EvidenceBuffer, EvidenceConfig, EvidenceWriter
from mobisentra.events.sink import EventRow
from mobisentra.ingestion.config import CameraConfig, ZoneType
from mobisentra.vision.track_history import TrackHistory
from mobisentra.vision.tracker import TrackedPerson

ZONE_COLORS: Final[dict[ZoneType, tuple[int, int, int]]] = {
    ZoneType.OCCUPANCY: (255, 160, 0),
    ZoneType.RESTRICTED: (0, 0, 255),
    ZoneType.DOOR: (0, 200, 255),
    ZoneType.REST: (160, 0, 160),
}


class CameraAnalytics:
    """One instance per camera; ``process`` once per analyzed frame."""

    def __init__(
        self,
        camera: CameraConfig,
        history: TrackHistory | None = None,
        evidence_root: Path | None = None,
        fall_config: FallConfig | None = None,
        evidence_config: EvidenceConfig | None = None,
    ) -> None:
        self._camera_id = camera.id
        self._zones = dict(camera.zones)
        self._zone_engine = ZoneEngine(self._zones)
        self._dwell = DwellTracker(camera)
        self._occupancy: dict[str, OccupancyMonitor] = {}
        self._last_band: dict[str, OccupancyBand] = {}
        self._latest_count: dict[str, int] = {}
        for name, zone in self._zones.items():
            if zone.zone_type is not ZoneType.OCCUPANCY:
                continue
            capacity = zone.max_capacity
            assert capacity is not None  # parser enforces this for occupancy zones
            self._occupancy[name] = OccupancyMonitor(
                max_capacity=capacity,
                confirm_frames=camera.thresholds.occupancy_confirm_frames,
            )
        self._history = history
        self._evidence_config = evidence_config or EvidenceConfig()
        self._fall = None if history is None else FallDetector(fall_config)
        self._evidence_buffer = EvidenceBuffer(self._evidence_config)
        self._evidence_writer = (
            None if evidence_root is None else EvidenceWriter(evidence_root, self._evidence_config)
        )

    def process(self, ts: float, frame: np.ndarray, people: list[TrackedPerson]) -> list[EventRow]:
        """One analyzed frame → candidate event rows (may be empty)."""
        membership = self._zone_engine.update(frame, people)
        rows: list[EventRow] = []
        for name, monitor in self._occupancy.items():
            reading = monitor.update(len(membership[name]))
            self._latest_count[name] = reading.count
            previous = self._last_band.get(name)
            if previous is not None and reading.band is not previous:
                rows.append(
                    EventRow(
                        kind="occupancy_level_change",
                        camera_id=self._camera_id,
                        zone=name,
                        from_band=str(previous),
                        to_band=str(reading.band),
                        count=reading.count,
                        ratio=reading.ratio,
                        ts=ts,
                    )
                )
            self._last_band[name] = reading.band
        for event in self._dwell.update(ts, membership):
            row = EventRow(
                kind=str(event.kind),
                camera_id=event.camera_id,
                zone=event.zone,
                track_id=event.track_id,
                dwell_seconds=event.dwell_seconds,
                first_seen_ts=event.first_seen_ts,
                ts=event.ts,
            )
            if event.kind is DwellEventKind.DOOR_OBSTRUCTION:
                row["door_state"] = event.door_state
            rows.append(row)
        if self._fall is not None and self._history is not None:
            rest_tracks = self._rest_tracks(membership)
            if self._evidence_writer is not None:
                self._evidence_buffer.push(ts, frame)
            rows.extend(self._fall_rows(ts, rest_tracks))
        return rows

    def forget(self, track_ids: list[int]) -> None:
        """Drop per-track cascade state for vanished tracks (the run loop
        calls this with TrackHistory.purge() results — long-running
        deployments must not accumulate dead-track state)."""
        if self._fall is not None:
            for track_id in track_ids:
                self._fall.forget(track_id)

    def _rest_tracks(self, membership: dict[str, set[int]]) -> set[int]:
        """Track IDs inside a rest zone this frame — lying is expected there
        (beds/berths), so the fall cascade is suppressed for them (UR Fall
        hard-negative mitigation, option a, 2026-08-27)."""
        suppressed: set[int] = set()
        for name, zone in self._zones.items():
            if zone.zone_type is ZoneType.REST:
                suppressed |= membership.get(name, set())
        return suppressed

    def _fall_rows(self, now_ts: float, suppressed: set[int]) -> list[EventRow]:
        pose_map = {
            track_id: self._history.pose_history(track_id) for track_id in self._history.track_ids()
        }
        rows: list[EventRow] = []
        for track_id, samples in pose_map.items():
            if track_id in suppressed:
                continue
            others = {
                other_id: other for other_id, other in pose_map.items() if other_id != track_id
            }
            candidates = self._fall.update(track_id, samples, now_ts=now_ts, others=others)
            for candidate in candidates:
                rows.append(self._fall_row(candidate, track_id))
        return rows

    def pending_fall_track_ids(self) -> list[int]:
        """Tracks mid-cascade (benchmark telemetry)."""
        return [] if self._fall is None else self._fall.pending_track_ids()

    def _fall_row(self, candidate: FallCandidate, track_id: int) -> EventRow:
        row = EventRow(
            kind=FALL_EVENT_KIND,
            camera_id=self._camera_id,
            track_id=track_id,
            ts=candidate.ts,
            trigger_ts=candidate.trigger_ts,
            confidence=candidate.confidence,
        )
        if self._evidence_writer is not None and self._history is not None:
            start_ts = candidate.trigger_ts - self._evidence_config.pre_trigger_seconds
            frames = self._evidence_buffer.snapshot(start_ts)
            pose_samples = [
                sample for sample in self._history.pose_history(track_id) if sample.ts >= start_ts
            ]
            path = self._evidence_writer.write_fall_clip(
                camera_id=self._camera_id,
                track_id=track_id,
                trigger_ts=candidate.trigger_ts,
                frames=frames,
                pose_samples=pose_samples,
            )
            self._evidence_writer.enforce_retention(self._camera_id)
            row["evidence_ref"] = str(path)
        return row

    def draw_overlay(self, image: np.ndarray) -> None:
        """Paint zone polygons + labels onto a preview frame (in place)."""
        height, width = image.shape[:2]
        for name, zone in self._zones.items():
            points = np.array(
                [[round(x * width), round(y * height)] for x, y in zone.polygon],
                dtype=np.int32,
            )
            color = ZONE_COLORS[zone.zone_type]
            cv2.polylines(image, [points], isClosed=True, color=color, thickness=2)
            origin = (int(points[0][0]), max(14, int(points[0][1]) - 6))
            label = name
            if name in self._occupancy and name in self._latest_count:
                capacity = self._zones[name].max_capacity
                assert capacity is not None
                band = str(self._last_band[name])
                label = f"{name} {band} {self._latest_count[name]}/{capacity}"
            cv2.putText(
                image,
                label,
                origin,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
            )
