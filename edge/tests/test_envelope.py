"""Envelope builder tests (Phase 6, Step 6.1a).

Every emitted kind round-trips through the shared schemas (Draft-07, the
``test_schemas`` pattern); determinism, fail-fast rejection, and the
constants-vs-schema drift guard.
"""

from __future__ import annotations

import json
from itertools import count
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from mobisentra.events.envelope import EVENT_TYPES, SEVERITIES, EnvelopeBuilder, to_payload
from mobisentra.events.sink import EventRow

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "events" / "v0"

MODEL_VERSIONS = {"detector": "yolo26n-pose@v0.1.0", "action": "movinet-a2@abc12345"}


@pytest.fixture(scope="module")
def envelope_validator() -> Draft7Validator:
    schema = json.loads((SCHEMAS / "envelope.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


@pytest.fixture(scope="module")
def event_validator() -> Draft7Validator:
    schema = json.loads((SCHEMAS / "event.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


@pytest.fixture
def builder() -> EnvelopeBuilder:
    counter = count(1)
    return EnvelopeBuilder(
        source="/mobisentra/edge/BUS_102/BUS_102_CAM_04",
        model_versions=MODEL_VERSIONS,
        id_factory=lambda: f"00000000-0000-4000-8000-{next(counter):012d}",
    )


# One representative row per kind CameraAnalytics emits today (5 of the 7
# enum kinds; overcrowding/person_down stay reserved until 6.2/Phase 10).
REPRESENTATIVE_ROWS: list[tuple[str, EventRow]] = [
    (
        "fall_detected",
        EventRow(
            kind="fall_detected",
            camera_id="BUS_102_CAM_04",
            track_id=27,
            ts=1750000000.5,
            trigger_ts=1750000000.0,
            confidence=0.94,
            evidence_ref="local://evidence/BUS_102_CAM_04/fall_track27.mp4",
        ),
    ),
    (
        "altercation_suspected",
        EventRow(
            kind="altercation_suspected",
            camera_id="BUS_102_CAM_04",
            track_a=4,
            track_b=11,
            ts=1750000012.0,
            trigger_ts=1750000011.4,
            confidence=0.81,
            action_score=0.86,
        ),
    ),
    (
        "restricted_zone_entry",
        EventRow(
            kind="restricted_zone_entry",
            camera_id="BUS_102_CAM_04",
            zone="track_edge_zone",
            track_id=9,
            ts=1750000020.0,
            first_seen_ts=1749999980.0,
            dwell_seconds=40.0,
        ),
    ),
    (
        "door_obstruction",
        EventRow(
            kind="door_obstruction",
            camera_id="BUS_102_CAM_04",
            zone="door_roi",
            track_id=3,
            ts=1750000030.0,
            dwell_seconds=3.0,
            door_state="closed",
        ),
    ),
    (
        "occupancy_level_change",
        EventRow(
            kind="occupancy_level_change",
            camera_id="BUS_102_CAM_04",
            zone="bus_area",
            from_band="crowded",
            to_band="over",
            count=61,
            ratio=1.22,
            ts=1750000040.0,
        ),
    ),
]


ROW_IDS = [kind for kind, _ in REPRESENTATIVE_ROWS]


@pytest.mark.parametrize(("kind", "row"), REPRESENTATIVE_ROWS, ids=ROW_IDS)
class TestEachKindValidates:
    def test_envelope_valid(self, builder, envelope_validator, kind, row):
        envelope = builder.build(row, severity="HIGH")
        assert envelope_validator.is_valid(envelope)
        assert envelope["type"] == f"org.mobisentra.event.{kind}"

    def test_payload_valid(self, event_validator, kind, row):
        payload = to_payload(row, severity="MEDIUM", model_versions=MODEL_VERSIONS)
        assert event_validator.is_valid(payload)


def test_tracks_mapping():
    by_kind = dict(REPRESENTATIVE_ROWS)

    def tracks_of(kind: str) -> object:
        return to_payload(by_kind[kind], severity="HIGH", model_versions={})["tracks"]

    assert tracks_of("fall_detected") == [27]
    assert tracks_of("altercation_suspected") == [4, 11]
    assert tracks_of("restricted_zone_entry") == [9]
    assert tracks_of("occupancy_level_change") == []


def test_location_zone_or_null():
    by_kind = dict(REPRESENTATIVE_ROWS)
    zoned = to_payload(by_kind["door_obstruction"], severity="MEDIUM", model_versions={})
    unzoned = to_payload(by_kind["fall_detected"], severity="HIGH", model_versions={})
    assert zoned["location"] == "door_roi"
    assert unzoned["location"] is None


def test_confidence_default_and_passthrough():
    by_kind = dict(REPRESENTATIVE_ROWS)
    rule_based = to_payload(by_kind["restricted_zone_entry"], severity="LOW", model_versions={})
    model_backed = to_payload(by_kind["fall_detected"], severity="HIGH", model_versions={})
    assert rule_based["confidence"] == 1.0
    assert model_backed["confidence"] == 0.94


def test_timestamp_iso_from_epoch():
    payload = to_payload(
        EventRow(kind="fall_detected", camera_id="C", ts=0.0),
        severity="HIGH",
        model_versions={},
    )
    assert payload["timestamp"] == "1970-01-01T00:00:00Z"
    envelope = EnvelopeBuilder(source="/mobisentra/edge/V/C", model_versions={}).build(
        EventRow(kind="fall_detected", camera_id="C", ts=0.0), severity="HIGH"
    )
    # envelope time is the occurrence time, identical to the payload timestamp
    assert envelope["time"] == payload["timestamp"]


def test_deterministic_build(builder):
    row = REPRESENTATIVE_ROWS[0][1]
    first = json.dumps(builder.build(row, severity="HIGH"), sort_keys=False)
    fresh = EnvelopeBuilder(
        source=builder.source,
        model_versions=MODEL_VERSIONS,
        id_factory=lambda: "fixed-id",
    )
    a = json.dumps(fresh.build(row, severity="HIGH"))
    b = json.dumps(fresh.build(row, severity="HIGH"))
    assert first  # built with the counter factory — distinct ids per call
    assert a == b  # fixed factory → byte-identical envelopes


def test_unknown_kind_rejected():
    with pytest.raises(ValueError, match="unknown event type"):
        to_payload(
            EventRow(kind="panic_detected", camera_id="C", ts=1.0),
            severity="HIGH",
            model_versions={},
        )


def test_non_enum_severity_rejected():
    with pytest.raises(ValueError, match="severity"):
        to_payload(
            EventRow(kind="fall_detected", camera_id="C", ts=1.0),
            severity="EXTREME",  # type: ignore[arg-type]
            model_versions={},
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.5])
def test_out_of_range_confidence_rejected(confidence):
    with pytest.raises(ValueError, match="confidence"):
        to_payload(
            EventRow(kind="fall_detected", camera_id="C", ts=1.0, confidence=confidence),
            severity="HIGH",
            model_versions={},
        )


@pytest.mark.parametrize("key", ["kind", "camera_id", "ts"])
def test_missing_required_keys_rejected(key):
    row = EventRow(kind="fall_detected", camera_id="C", ts=1.0)
    del row[key]
    with pytest.raises(ValueError, match="missing required key"):
        to_payload(row, severity="HIGH", model_versions={})


def test_bad_source_rejected():
    with pytest.raises(ValueError, match="source"):
        EnvelopeBuilder(source="BUS_102", model_versions={})


def test_model_versions_stamped_and_copied():
    versions = {"detector": "x@1"}
    builder = EnvelopeBuilder(source="/mobisentra/edge/V/C", model_versions=versions)
    envelope = builder.build(EventRow(kind="fall_detected", camera_id="C", ts=1.0), severity="HIGH")
    assert envelope["data"]["model_versions"] == {"detector": "x@1"}
    versions["detector"] = "mutated"  # builder must hold its own copy
    assert envelope["data"]["model_versions"] == {"detector": "x@1"}


def test_event_type_override_for_6_2_mapping():
    row = dict(REPRESENTATIVE_ROWS)[  # occupancy into-`over` → reserved kind
        "occupancy_level_change"
    ]
    envelope = EnvelopeBuilder(source="/mobisentra/edge/V/C", model_versions={}).build(
        row, severity="MEDIUM", event_type="overcrowding"
    )
    assert envelope["type"] == "org.mobisentra.event.overcrowding"
    assert envelope["data"]["event_type"] == "overcrowding"
    assert envelope["data"]["to_band"] == "over"  # diagnostics ride along


def test_diagnostics_passthrough_allowlist_only():
    payload = to_payload(
        EventRow(
            kind="fall_detected", camera_id="C", ts=1.0, trigger_ts=0.5, mystery_key="x"
        ),
        severity="HIGH",
        model_versions={},
    )
    assert payload["trigger_ts"] == 0.5
    assert "mystery_key" not in payload


def test_constants_match_shared_schemas():
    """Drift guard: the code enums must equal the schema enums exactly."""
    event_schema = json.loads((SCHEMAS / "event.schema.json").read_text())
    assert tuple(event_schema["properties"]["event_type"]["enum"]) == EVENT_TYPES
    assert tuple(event_schema["properties"]["severity"]["enum"]) == SEVERITIES
