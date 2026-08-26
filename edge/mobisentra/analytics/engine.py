"""Camera analytics composition (Phase 3, Day 4 wiring).

Bundles the Step 3.1–3.4 pieces behind one per-camera object:
``ZoneEngine`` membership → ``OccupancyMonitor`` bands (emit
``occupancy_level_change`` on confirmed flips — the band a camera starts
in is not an event) + ``DwellTracker`` loiter/obstruction events.
``process`` returns JSONL-ready rows; ``draw_overlay`` renders zone
boundaries for ``--preview``.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np

from mobisentra.analytics.occupancy import OccupancyBand, OccupancyMonitor
from mobisentra.analytics.zone_events import DwellEventKind, DwellTracker
from mobisentra.analytics.zones import ZoneEngine
from mobisentra.events.sink import EventRow
from mobisentra.ingestion.config import CameraConfig, ZoneType
from mobisentra.vision.tracker import TrackedPerson

ZONE_COLORS: Final[dict[ZoneType, tuple[int, int, int]]] = {
    ZoneType.OCCUPANCY: (255, 160, 0),
    ZoneType.RESTRICTED: (0, 0, 255),
    ZoneType.DOOR: (0, 200, 255),
}


class CameraAnalytics:
    """One instance per camera; ``process`` once per analyzed frame."""

    def __init__(self, camera: CameraConfig) -> None:
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
        return rows

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
