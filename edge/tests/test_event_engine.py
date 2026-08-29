"""Event engine tests (Phase 6, Step 6.1b).

The plan's done-when list, one test each, plus schema round-trips, the
purity guard (Gate 6, delivered early), and determinism.
"""

from __future__ import annotations

import ast
import json
from itertools import count
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from mobisentra.events.engine import (
    DWELL_KINDS,
    OCCUPANCY_KINDS,
    DebouncePolicy,
    EventEngine,
    Resolution,
)
from mobisentra.events.envelope import EnvelopeBuilder
from mobisentra.events.sink import EventRow

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "events" / "v0"

COOLDOWNS = {
    "fall_detected": 300.0,
    "altercation_suspected": 180.0,
    "overcrowding": 120.0,
    "occupancy_level_change": 120.0,
    "restricted_zone_entry": 600.0,
    "door_obstruction": 300.0,
}


def make_policy() -> DebouncePolicy:
    return DebouncePolicy(
        cooldowns_s=COOLDOWNS,
        fight_escalation_window_s=600.0,
        fight_escalation_severity="CRITICAL",
    )


def make_engine() -> EventEngine:
    counter = count(1)

    def resolve(row: EventRow) -> Resolution:
        # stub severity mapper: 6.2 replaces this with the severity.yaml rules
        if row["kind"] == "occupancy_level_change" and row.get("to_band") == "overcrowded":
            return Resolution("overcrowding", "MEDIUM")
        base = {
            "fall_detected": "HIGH",
            "altercation_suspected": "HIGH",
            "restricted_zone_entry": "LOW",
            "door_obstruction": "MEDIUM",
        }
        return Resolution(row["kind"], base.get(row["kind"], "LOW"))

    return EventEngine(
        builder=EnvelopeBuilder(
            source="/mobisentra/edge/BUS_102/BUS_102_CAM_04",
            model_versions={"detector": "yolo26n-pose@v0.1.0"},
            id_factory=lambda: f"00000000-0000-4000-8000-{next(counter):012d}",
        ),
        policy=make_policy(),
        resolver=resolve,
    )


def fall(ts: float, track: int = 27) -> EventRow:
    return EventRow(kind="fall_detected", camera_id="C", track_id=track, ts=ts, confidence=0.9)


def fight(ts: float, a: int = 4, b: int = 11) -> EventRow:
    return EventRow(
        kind="altercation_suspected", camera_id="C", track_a=a, track_b=b, ts=ts, confidence=0.8
    )


def occ(ts: float, from_band: str, to_band: str, zone: str = "bus_area") -> EventRow:
    return EventRow(
        kind="occupancy_level_change",
        camera_id="C",
        zone=zone,
        from_band=from_band,
        to_band=to_band,
        ts=ts,
    )


def dwell(ts: float, track: int, zone: str = "track_edge_zone") -> EventRow:
    return EventRow(kind="restricted_zone_entry", camera_id="C", zone=zone, track_id=track, ts=ts)


def severities(envelopes: list[dict[str, object]]) -> list[object]:
    return [envelope["data"]["severity"] for envelope in envelopes]


# --- plan done-when list -----------------------------------------------------


def test_repeated_falls_same_track_within_cooldown_emit_once():
    engine = make_engine()
    emitted = engine.process([fall(100.0), fall(110.0), fall(200.0)])  # all < 300 s apart
    assert len(emitted) == 1
    assert engine.suppressed == {"fall_detected/27": 2}


def test_different_tracks_both_emit():
    engine = make_engine()
    emitted = engine.process([fall(100.0, track=1), fall(105.0, track=2)])
    assert len(emitted) == 2
    assert engine.suppressed == {}


def test_fall_rearms_after_cooldown_expiry():
    engine = make_engine()
    emitted = engine.process([fall(100.0), fall(399.0), fall(500.0)])  # 400 ≥ 300 cooldown
    assert len(emitted) == 2


def test_occupancy_flicker_no_duplicate_transitions():
    engine = make_engine()
    rows = [
        occ(100.0, "crowded", "overcrowded"),  # escalate → overcrowding
        occ(130.0, "crowded", "overcrowded"),  # duplicate transition → suppressed
        occ(160.0, "overcrowded", "crowded"),  # de-escalate → emitted (exempt)
        occ(190.0, "crowded", "overcrowded"),  # re-armed → fresh escalation emits
    ]
    emitted = engine.process(rows)
    types = [envelope["data"]["event_type"] for envelope in emitted]
    assert types == ["overcrowding", "occupancy_level_change", "overcrowding"]
    occupancy_family_key = "occupancy/bus_area"  # coalesced across both occupancy kinds
    assert engine.suppressed == {occupancy_family_key: 1}


def test_deescalation_never_suppressed_even_immediately_after_escalation():
    engine = make_engine()
    rows = [occ(100.0, "crowded", "overcrowded"), occ(101.0, "overcrowded", "normal")]
    emitted = engine.process(rows)
    assert [envelope["data"]["event_type"] for envelope in emitted] == [
        "overcrowding",
        "occupancy_level_change",
    ]
    assert engine.suppressed == {}


def test_deescalation_rearms_escalation_key():
    engine = make_engine()
    rows = [
        occ(100.0, "crowded", "overcrowded"),  # emit
        occ(110.0, "overcrowded", "crowded"),  # de-escalate (re-arm), 10 s later
        occ(120.0, "crowded", "overcrowded"),  # 20 s after first fire < cooldown
    ]
    emitted = engine.process(rows)
    assert len(emitted) == 3  # re-arm means the third is a fresh incident


def test_fight_refire_after_cooldown_within_window_escalates():
    engine = make_engine()
    emitted = engine.process([fight(100.0), fight(400.0)])  # 300 s: past cooldown, ≤ 600 s window
    assert severities(emitted) == ["HIGH", "CRITICAL"]


def test_fight_refire_after_window_is_fresh():
    engine = make_engine()
    emitted = engine.process([fight(100.0), fight(800.0)])  # 700 s > 600 s window
    assert severities(emitted) == ["HIGH", "HIGH"]


def test_fight_refire_within_cooldown_suppressed_not_escalated():
    engine = make_engine()
    emitted = engine.process([fight(100.0), fight(200.0)])  # 100 s < 180 s cooldown
    assert severities(emitted) == ["HIGH"]
    assert engine.suppressed == {"altercation_suspected/4/11": 1}


def test_fight_pair_key_order_proof():
    engine = make_engine()
    emitted = engine.process([fight(100.0, a=4, b=11), fight(150.0, a=11, b=4)])
    assert len(emitted) == 1  # same pair regardless of track order
    assert engine.suppressed == {"altercation_suspected/4/11": 1}


def test_dwell_subject_granularity_is_zone_and_track():
    engine = make_engine()
    emitted = engine.process([dwell(100.0, track=9), dwell(110.0, track=15)])
    assert len(emitted) == 2  # second person = second incident, not spam
    assert engine.suppressed == {}


def test_unknown_kind_after_resolution_fails_fast():
    engine = make_engine()
    with pytest.raises(ValueError, match="no debounce policy"):
        engine.process([EventRow(kind="panic_detected", camera_id="C", ts=1.0)])


def test_engine_reset_clears_state():
    engine = make_engine()
    engine.process([fall(100.0)])
    assert engine.suppressed == {}
    engine.process([fall(110.0)])
    assert engine.suppressed == {"fall_detected/27": 1}
    engine.reset()
    engine.process([fall(120.0)])
    assert engine.suppressed == {}


# --- schema round-trip + determinism ------------------------------------------


@pytest.fixture(scope="module")
def envelope_validator() -> Draft7Validator:
    schema = json.loads((SCHEMAS / "envelope.schema.json").read_text())
    return Draft7Validator(schema)


def test_every_emitted_envelope_validates_against_schema(envelope_validator):
    engine = make_engine()
    rows = [
        fall(100.0),
        fight(110.0),
        dwell(120.0, track=3),
        EventRow(
            kind="door_obstruction",
            camera_id="C",
            zone="door_roi",
            track_id=5,
            ts=130.0,
            door_state="closed",
        ),
        occ(140.0, "crowded", "overcrowded"),
        occ(150.0, "overcrowded", "crowded"),
    ]
    for envelope in engine.process(rows):
        assert envelope_validator.is_valid(envelope), envelope["id"]


def test_deterministic_output_with_fixed_id_factory():
    def build_all() -> str:
        counter = count(1)
        engine = EventEngine(
            builder=EnvelopeBuilder(
                source="/mobisentra/edge/V/C",
                model_versions={"detector": "x@1"},
                id_factory=lambda: f"00000000-0000-4000-8000-{next(counter):012d}",
            ),
            policy=make_policy(),
            resolver=lambda row: Resolution(row["kind"], "HIGH"),
        )
        return json.dumps(engine.process([fall(100.0), fall(110.0), fall(400.0)]))

    assert build_all() == build_all()


# --- purity guard (Gate 6, delivered with 6.1b) --------------------------------

ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "mobisentra.analytics.occupancy",
    "mobisentra.events.envelope",
    "mobisentra.events.sink",
}


def test_event_engine_is_pure_no_io_or_heavy_imports():
    """Gate 6 criterion: no camera/network/DB/cv calls can even be imported.
    Extending the allowlist is a deliberate, reviewable act."""
    source = Path(mobisentra_events_engine_file()).read_text()
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            offenders.extend(
                alias.name for alias in node.names if alias.name not in ALLOWED_IMPORTS
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module not in ALLOWED_IMPORTS:
                offenders.append(module)
    assert offenders == [], f"engine.py grew non-pure imports: {offenders}"


def mobisentra_events_engine_file() -> str:
    import mobisentra.events.engine as engine_module

    return str(engine_module.__file__)


def test_kind_constants_cover_the_policy():
    """Every policy kind must be a schema enum member (drift guard for tests)."""
    from mobisentra.events.envelope import EVENT_TYPES

    for kind in COOLDOWNS:
        assert kind in EVENT_TYPES
    assert DWELL_KINDS | OCCUPANCY_KINDS <= set(EVENT_TYPES)
