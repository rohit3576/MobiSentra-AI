"""Golden-file tests for the event engine (Phase 6, Step 6.4 — Gate 6).

Replays each ``tests/golden/*.json`` scenario through the real severity
resolver + ``EventEngine`` with a fixed id-factory and compares the emitted
envelope stream byte-exactly. Regeneration (deliberate contract changes):
``GOLDEN_REGEN=1 pytest tests/test_golden.py`` — then review the diff; see
``tests/golden/README.md``.
"""

from __future__ import annotations

import json
import os
import re
from itertools import count
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from mobisentra.events.engine import EventEngine
from mobisentra.events.envelope import EnvelopeBuilder
from mobisentra.events.severity import SeverityPolicy, make_resolver
from mobisentra.events.sink import EventRow

GOLDENS = Path(__file__).parent / "golden"
SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "events" / "v0"
GOLDEN_ID = re.compile(r"^gold-\d{3}$")


@pytest.fixture(scope="module")
def validators() -> tuple[Draft7Validator, Draft7Validator]:
    envelope = Draft7Validator(json.loads((SCHEMAS / "envelope.schema.json").read_text()))
    event = Draft7Validator(json.loads((SCHEMAS / "event.schema.json").read_text()))
    return envelope, event


def policy_from(golden: dict) -> SeverityPolicy:
    raw = golden["policy"]
    return SeverityPolicy(
        severities=raw["severities"],
        overcrowding_escalates_at=raw["overcrowding_escalates_at"],
        overcrowding_escalates_to=raw["overcrowding_escalates_to"],
        cooldowns_s=raw["cooldowns_s"],
        fight_escalation_window_s=raw["fight_escalation_window_s"],
        fight_escalation_severity=raw["fight_escalation_severity"],
    )


def replay(golden: dict) -> tuple[list[dict[str, object]], dict[str, int]]:
    policy = policy_from(golden)
    counter = count(1)
    engine = EventEngine(
        builder=EnvelopeBuilder(
            source=golden["source"],
            model_versions=golden["model_versions"],
            id_factory=lambda: f"gold-{next(counter):03d}",
        ),
        policy=policy.debounce(),
        resolver=make_resolver(policy),
    )
    emitted: list[dict[str, object]] = []
    for frame_rows in golden["input_frames"]:
        emitted.extend(engine.process([EventRow(**row) for row in frame_rows]))
    return emitted, engine.suppressed


GOLDEN_FILES = sorted(GOLDENS.glob("*.json"))


@pytest.mark.parametrize("golden_path", GOLDEN_FILES, ids=[p.stem for p in GOLDEN_FILES])
def test_golden_stream(golden_path: Path, validators) -> None:
    golden = json.loads(golden_path.read_text())
    emitted, suppressed = replay(golden)

    if os.environ.get("GOLDEN_REGEN"):
        golden["expected_envelopes"] = emitted
        golden["expected_suppressed"] = suppressed
        golden_path.write_text(json.dumps(golden, indent=2) + "\n")
        return

    assert [(env["id"], env["type"], env["data"]["severity"]) for env in emitted] == [
        (env["id"], env["type"], env["data"]["severity"])
        for env in golden["expected_envelopes"]
    ], f"{golden_path.stem}: stream diverged — regenerate only after review (see golden/README.md)"
    assert emitted == golden["expected_envelopes"], f"{golden_path.stem}: envelope bytes diverged"
    assert suppressed == golden["expected_suppressed"], f"{golden_path.stem}: suppression diverged"

    envelope_validator, event_validator = validators
    for envelope in golden["expected_envelopes"]:
        assert GOLDEN_ID.match(str(envelope["id"])), "goldens must use the fixed id-factory"
        assert envelope_validator.is_valid(envelope), envelope["id"]
        assert event_validator.is_valid(envelope["data"]), envelope["id"]
