"""ZoneEngine membership tests (Phase 3, Step 3.1).

Synthetic boxes + polygons only — no video, no model. Membership is decided
by the BOTTOM_CENTER (feet) anchor: physical presence in a zone means feet
inside, boxes merely overlapping the zone edge do not count.
"""

import numpy as np
import pytest

from mobisentra.analytics.zones import ZoneEngine
from mobisentra.ingestion.config import ZoneConfig, ZoneType
from mobisentra.vision.tracker import TrackedPerson

# Normalized square zone x/y in [0.25, 0.75] → pixels (50..150, 25..75) on
# the 200x100 frame used throughout.
SQUARE = ((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75))
FRAME = np.zeros((100, 200, 3), dtype=np.uint8)


def restricted_zone(name: str = "z", polygon=SQUARE) -> ZoneConfig:
    return ZoneConfig(name=name, zone_type=ZoneType.RESTRICTED, polygon=polygon)


def person(track_id: int, x1: float, y1: float, x2: float, y2: float) -> TrackedPerson:
    return TrackedPerson(track_id=track_id, bbox=(x1, y1, x2, y2), confidence=0.9)


def test_feet_inside_zone_counts_as_member():
    engine = ZoneEngine({"z": restricted_zone()})
    members = engine.update(FRAME, [person(1, 60, 20, 80, 50)])
    assert members == {"z": {1}}


def test_box_overlapping_edge_but_feet_outside_is_not_member():
    engine = ZoneEngine({"z": restricted_zone()})
    # Box straddles the zone's bottom edge (y=75); feet at (70, 90) are below.
    members = engine.update(FRAME, [person(1, 60, 30, 80, 90)])
    assert members == {"z": set()}


def test_person_fully_outside_is_not_member():
    engine = ZoneEngine({"z": restricted_zone()})
    members = engine.update(FRAME, [person(1, 10, 5, 30, 15)])
    assert members == {"z": set()}


def test_mask_zips_with_track_ids_not_boxes():
    engine = ZoneEngine({"z": restricted_zone()})
    members = engine.update(
        FRAME,
        [person(7, 10, 5, 30, 15), person(3, 60, 20, 80, 50), person(9, 180, 80, 195, 95)],
    )
    assert members == {"z": {3}}


def test_empty_people_yields_empty_sets_per_zone():
    engine = ZoneEngine({"a": restricted_zone("a"), "b": restricted_zone("b")})
    assert engine.update(FRAME, []) == {"a": set(), "b": set()}


def test_polygon_denormalized_against_frame_size():
    # Same normalized zone on a doubled frame (400x200): pixel square is
    # (100..300, 50..150). A person with feet at (200, 100) is a member.
    big_frame = np.zeros((200, 400, 3), dtype=np.uint8)
    engine = ZoneEngine({"z": restricted_zone()})
    members = engine.update(big_frame, [person(1, 180, 60, 220, 100)])
    assert members == {"z": {1}}


def test_frame_size_change_triggers_re_denormalization(caplog):
    engine = ZoneEngine({"z": restricted_zone()})
    engine.update(FRAME, [])
    big_frame = np.zeros((200, 400, 3), dtype=np.uint8)
    with caplog.at_level("WARNING", logger="mobisentra.analytics.zones"):
        members = engine.update(big_frame, [person(1, 180, 60, 220, 100)])
    assert members == {"z": {1}}
    assert any("frame size changed" in record.message for record in caplog.records)


def test_same_frame_size_does_not_warn(caplog):
    engine = ZoneEngine({"z": restricted_zone()})
    engine.update(FRAME, [])
    with caplog.at_level("WARNING", logger="mobisentra.analytics.zones"):
        engine.update(FRAME, [])
    assert not caplog.records


def test_multiple_zones_evaluated_independently():
    left = restricted_zone("left", ((0.0, 0.0), (0.4, 0.0), (0.4, 1.0), (0.0, 1.0)))
    right = restricted_zone("right", ((0.6, 0.0), (1.0, 0.0), (1.0, 1.0), (0.6, 1.0)))
    engine = ZoneEngine({"left": left, "right": right})
    members = engine.update(FRAME, [person(1, 20, 10, 60, 90), person(2, 140, 10, 170, 90)])
    assert members == {"left": {1}, "right": {2}}


def test_person_can_be_member_of_overlapping_zones():
    overlap = restricted_zone("wide", ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    inner = restricted_zone("inner", SQUARE)
    engine = ZoneEngine({"wide": overlap, "inner": inner})
    members = engine.update(FRAME, [person(1, 60, 20, 80, 50)])
    assert members == {"wide": {1}, "inner": {1}}


@pytest.mark.parametrize(
    ("bbox", "expected"),
    [
        ((60, 20, 80, 50), True),  # feet (70, 50) clearly inside
        ((60, 30, 80, 90), False),  # feet (70, 90) clearly below zone
        ((10, 30, 40, 70), False),  # feet (25, 70) left of zone
    ],
)
def test_membership_boundary_cases(bbox, expected):
    engine = ZoneEngine({"z": restricted_zone()})
    members = engine.update(FRAME, [person(1, *bbox)])
    assert (1 in members["z"]) is expected
