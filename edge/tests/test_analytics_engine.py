"""CameraAnalytics composition tests (Phase 3, Day 4 wiring).

ZoneEngine + OccupancyMonitor + DwellTracker behind one ``process`` call,
returning JSONL-ready event rows. First occupancy reading establishes
silently (a band you start in is not a change); later confirmed flips emit
``occupancy_level_change`` rows.
"""

import numpy as np

from mobisentra.analytics.engine import CameraAnalytics
from mobisentra.ingestion.config import (
    CameraConfig,
    Thresholds,
    ZoneConfig,
    ZoneType,
)
from mobisentra.vision.tracker import TrackedPerson

SQUARE = ((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75))
FRAME = np.zeros((100, 200, 3), dtype=np.uint8)


def camera() -> CameraConfig:
    return CameraConfig(
        id="CAM_A",
        source="sample://videos/a.mp4",
        vehicle_id="V1",
        zones={
            "bus_area": ZoneConfig(
                name="bus_area",
                zone_type=ZoneType.OCCUPANCY,
                polygon=SQUARE,
                max_capacity=1,
            ),
            "no_go": ZoneConfig(name="no_go", zone_type=ZoneType.RESTRICTED, polygon=SQUARE),
        },
        thresholds=Thresholds(
            occupancy_confirm_frames=1,
            restricted_loiter_seconds=0.5,
            door_obstruct_seconds=0.25,
        ),
    )


def person(track_id: int, inside: bool) -> TrackedPerson:
    if inside:
        return TrackedPerson(track_id=track_id, bbox=(60, 20, 80, 50), confidence=0.9)
    return TrackedPerson(track_id=track_id, bbox=(10, 5, 30, 15), confidence=0.9)


def process_all(analytics: CameraAnalytics, samples: list[tuple[float, list[TrackedPerson]]]):
    rows = []
    for ts, people in samples:
        rows.extend(analytics.process(ts, FRAME, people))
    return rows


def test_first_occupancy_reading_establishes_silently():
    rows = process_all(CameraAnalytics(camera()), [(0.0, [person(1, inside=True)])])
    assert rows == []


def test_confirmed_band_flip_emits_occupancy_level_change():
    analytics = CameraAnalytics(camera())
    rows = process_all(
        analytics,
        [(0.0, []), (0.25, [person(1, inside=True)])],  # 0/1 → 1/1: normal → crowded
    )
    changes = [r for r in rows if r["kind"] == "occupancy_level_change"]
    assert len(changes) == 1
    row = changes[0]
    assert row["camera_id"] == "CAM_A"
    assert row["zone"] == "bus_area"
    assert row["from_band"] == "normal"
    assert row["to_band"] == "crowded"
    assert row["count"] == 1
    assert row["ratio"] == 1.0
    assert row["ts"] == 0.25


def test_band_flip_back_emits_second_change():
    analytics = CameraAnalytics(camera())
    rows = process_all(
        analytics,
        [
            (0.0, []),
            (0.25, [person(1, inside=True)]),
            (0.5, []),
        ],
    )
    changes = [r for r in rows if r["kind"] == "occupancy_level_change"]
    assert [(c["from_band"], c["to_band"]) for c in changes] == [
        ("normal", "crowded"),
        ("crowded", "normal"),
    ]


def test_dwell_event_passes_through_as_row():
    analytics = CameraAnalytics(camera())
    rows = process_all(analytics, [(t / 4.0, [person(1, inside=True)]) for t in range(5)])
    entries = [r for r in rows if r["kind"] == "restricted_zone_entry"]
    assert len(entries) == 1
    row = entries[0]
    assert row["camera_id"] == "CAM_A"
    assert row["zone"] == "no_go"
    assert row["track_id"] == 1
    assert row["dwell_seconds"] == 0.5
    assert row["ts"] == 0.5
    assert "door_state" not in row


def test_door_dwell_row_carries_reserved_door_state():
    config = camera()
    config.zones["dr"] = ZoneConfig(name="dr", zone_type=ZoneType.DOOR, polygon=SQUARE)
    analytics = CameraAnalytics(config)
    rows = process_all(
        analytics,
        [(0.0, [person(9, inside=True)]), (0.25, [person(9, inside=True)])],
    )
    entries = [r for r in rows if r["kind"] == "door_obstruction"]
    assert len(entries) == 1
    assert entries[0]["door_state"] == "unknown"


def test_draw_overlay_paints_zone_boundaries():
    analytics = CameraAnalytics(camera())
    canvas = np.zeros((100, 200, 3), dtype=np.uint8)
    analytics.draw_overlay(canvas)
    assert int(canvas.sum()) > 0


def test_camera_without_zones_yields_no_events():
    empty = CameraConfig(id="CAM_B", source="s", vehicle_id="V")
    analytics = CameraAnalytics(empty)
    rows = analytics.process(0.0, FRAME, [person(1, inside=True)])
    assert rows == []
