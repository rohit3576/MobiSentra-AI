"""Rest-zone annotator pure-logic tests (Phase 4.6 option A tooling).

The interactive loop needs a display and stays untested; the geometry and
JSON shape it produces are what the benchmark consumes, so those are
pinned here.
"""

from __future__ import annotations

import json

from tools.rest_zone_annotator import normalize_polygon


def test_normalize_polygon_divides_by_frame_size():
    points = [(100, 50), (300, 50), (300, 200), (100, 200)]
    normalized = normalize_polygon(points, width=400, height=250)
    assert normalized == [(0.25, 0.2), (0.75, 0.2), (0.75, 0.8), (0.25, 0.8)]


def test_annotation_file_round_trips_through_json():
    polygon = normalize_polygon([(10, 20), (90, 20), (90, 80), (10, 80)], 100, 100)
    payload = json.loads(json.dumps({"adl-01-cam0": [list(point) for point in polygon]}))
    restored = tuple(tuple(point) for point in payload["adl-01-cam0"])
    assert restored == ((0.1, 0.2), (0.9, 0.2), (0.9, 0.8), (0.1, 0.8))
