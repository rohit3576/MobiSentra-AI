"""Zone membership engine (Phase 3, Step 3.1).

Wraps ``supervision.PolygonZone`` per configured zone: tracked people →
zone-name → set of track IDs whose feet (BOTTOM_CENTER anchor) fall inside
the zone polygon. Polygons are stored normalized in the registry and
denormalized against the frame size captured on the first analyzed frame;
a later frame-size change logs a warning and re-denormalizes.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

import numpy as np
import supervision as sv

from mobisentra.ingestion.config import ZoneConfig
from mobisentra.vision.tracker import TrackedPerson

logger = logging.getLogger(__name__)


def _denormalize(polygon: tuple[tuple[float, float], ...], width: int, height: int) -> np.ndarray:
    return np.array([[round(x * width), round(y * height)] for x, y in polygon], dtype=np.int64)


class ZoneEngine:
    """Zone membership for one camera. Construct once, call ``update`` per frame."""

    def __init__(self, zones: Mapping[str, ZoneConfig]) -> None:
        self._zones = dict(zones)
        self._frame_size: tuple[int, int] | None = None
        self._sv_zones: dict[str, sv.PolygonZone] = {}

    def update(self, frame: np.ndarray, people: Sequence[TrackedPerson]) -> dict[str, set[int]]:
        """Return zone name → track IDs with feet inside that zone this frame."""
        self._sync_zones(frame.shape[1], frame.shape[0])
        if not people:
            return {name: set() for name in self._sv_zones}

        xyxy = np.array([p.bbox for p in people], dtype=np.float32)
        track_ids = np.array([p.track_id for p in people], dtype=np.int64)
        detections = sv.Detections(xyxy=xyxy, tracker_id=track_ids)

        return {
            name: {int(tid) for tid in track_ids[zone.trigger(detections)]}
            for name, zone in self._sv_zones.items()
        }

    def _sync_zones(self, width: int, height: int) -> None:
        size = (width, height)
        if self._frame_size == size:
            return
        if self._frame_size is not None:
            logger.warning(
                "frame size changed %s → %s; re-denormalizing zone polygons",
                self._frame_size,
                size,
            )
        self._sv_zones = {
            name: sv.PolygonZone(
                polygon=_denormalize(zone.polygon, width, height),
                triggering_anchors=(sv.Position.BOTTOM_CENTER,),
            )
            for name, zone in self._zones.items()
        }
        self._frame_size = size
