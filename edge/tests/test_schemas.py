"""Event schema v0 validation tests (Phase 0, Step 0.9).

Both the edge (Python) and backend (TypeScript) suites validate the same
shared schemas + example — this is the contract freeze test.
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "events" / "v0"


@pytest.fixture(scope="module")
def fall_envelope() -> dict:
    return json.loads((SCHEMAS / "examples" / "fall_envelope.json").read_text())


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


def test_all_schemas_are_valid_draft7():
    for path in SCHEMAS.glob("*.schema.json"):
        schema = json.loads(path.read_text())
        Draft7Validator.check_schema(schema)  # raises on invalid schema


def test_fall_envelope_valid(envelope_validator, fall_envelope):
    assert envelope_validator.is_valid(fall_envelope)


def test_fall_event_data_valid(event_validator, fall_envelope):
    assert event_validator.is_valid(fall_envelope["data"])


def test_envelope_requires_core_attributes(envelope_validator, fall_envelope):
    for attr in ("specversion", "id", "source", "type", "time", "data"):
        broken = {k: v for k, v in fall_envelope.items() if k != attr}
        assert not envelope_validator.is_valid(broken), f"missing {attr} should fail"


def test_envelope_rejects_bad_source_and_type(envelope_validator, fall_envelope):
    bad_source = {**fall_envelope, "source": "BUS_102"}
    bad_type = {**fall_envelope, "type": "random.event"}
    assert not envelope_validator.is_valid(bad_source)
    assert not envelope_validator.is_valid(bad_type)


def test_event_rejects_bad_severity_and_confidence(event_validator, fall_envelope):
    data = fall_envelope["data"]
    assert not event_validator.is_valid({**data, "severity": "EXTREME"})
    assert not event_validator.is_valid({**data, "confidence": 1.5})
