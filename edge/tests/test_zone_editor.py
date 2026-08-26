"""Zone editor round-trip tests (Phase 3, Step 3.5 — Gate 3 evidence).

Gate 3: polygon drawn with the editor → exported YAML snippet → loaded by
the registry parser → ZoneEngine membership matches the drawn region.
Clicks are simulated as pixel points; the GUI loop itself is thin glue
(polish explicitly out of scope, plan §9).
"""

from __future__ import annotations

import yaml

from mobisentra.analytics.zones import ZoneEngine
from mobisentra.ingestion.config import load_cameras
from mobisentra.vision.tracker import TrackedPerson
from tools.zone_editor import build_zones_yaml, normalize_points

# Editor canvas: 200x100 px. Clicked square (50,25)-(150,75).
CLICKS = [(50, 25), (150, 25), (150, 75), (50, 75)]
FRAME_W, FRAME_H = 200, 100


def test_normalize_points_scales_pixels_to_unit_square():
    normalized = normalize_points(CLICKS, FRAME_W, FRAME_H)
    assert normalized == ((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75))


def test_yaml_snippet_parses_as_zones_mapping():
    snippet = build_zones_yaml(
        name="no_go",
        zone_type="restricted",
        points=normalize_points(CLICKS, FRAME_W, FRAME_H),
    )
    doc = yaml.safe_load(snippet)
    zone = doc["zones"]["no_go"]
    assert zone["type"] == "restricted"
    assert zone["polygon"] == [
        [0.25, 0.25],
        [0.75, 0.25],
        [0.75, 0.75],
        [0.25, 0.75],
    ]
    assert "max_capacity" not in zone


def test_occupancy_snippet_carries_max_capacity():
    snippet = build_zones_yaml(
        name="bus_area",
        zone_type="occupancy",
        points=normalize_points(CLICKS, FRAME_W, FRAME_H),
        max_capacity=50,
    )
    assert yaml.safe_load(snippet)["zones"]["bus_area"]["max_capacity"] == 50


def person_feet_at(px: int, py: int) -> TrackedPerson:
    return TrackedPerson(track_id=1, bbox=(px - 5, py - 10, px + 5, py), confidence=0.9)


def test_round_trip_drawn_polygon_loads_and_matches_membership(tmp_path):
    snippet = build_zones_yaml(
        name="drawn",
        zone_type="restricted",
        points=normalize_points(CLICKS, FRAME_W, FRAME_H),
    )
    registry = {
        "cameras": [
            {
                "id": "CAM_RT",
                "source": "sample://videos/a.mp4",
                "vehicle_id": "V1",
                "zones": yaml.safe_load(snippet)["zones"],
            }
        ]
    }
    registry_file = tmp_path / "cameras.yaml"
    registry_file.write_text(yaml.safe_dump(registry))

    camera = load_cameras(registry_file)[0]
    engine = ZoneEngine(camera.zones)

    import numpy as np

    frame = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
    inside = engine.update(frame, [person_feet_at(100, 50)])[  # clicked square center
        "drawn"
    ]
    outside_corners = engine.update(frame, [person_feet_at(20, 10), person_feet_at(180, 90)])[
        "drawn"
    ]

    assert inside == {1}
    assert outside_corners == set()
