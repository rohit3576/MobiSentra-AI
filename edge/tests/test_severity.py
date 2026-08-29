"""Severity config tests (Phase 6, Step 6.2).

The runbook's done-when: **editing severity.yaml changes severity with zero
code changes** — proven by file round-trips through the same loader. Plus
strict fail-fast validation and the resolver rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mobisentra.events.engine import DebouncePolicy, EventEngine, Resolution
from mobisentra.events.envelope import EVENT_TYPES, EnvelopeBuilder
from mobisentra.events.severity import (
    EMITTABLE_KINDS,
    RESERVED_KINDS,
    SeverityConfigError,
    load_severity_policy,
    make_resolver,
)
from mobisentra.events.sink import EventRow

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "severity.yaml"


@pytest.fixture(scope="module")
def policy() -> object:
    return load_severity_policy(CONFIG)


@pytest.fixture(scope="module")
def resolver(policy) -> object:
    return make_resolver(policy)


def occ(to_band: str, ratio: float | None = None) -> EventRow:
    row = EventRow(
        kind="occupancy_level_change",
        camera_id="C",
        zone="bus_area",
        from_band="crowded",
        to_band=to_band,
        ts=100.0,
    )
    if ratio is not None:
        row["ratio"] = ratio
    return row


# --- shipped defaults (locked with the approved plan) --------------------------


def test_defaults_load_and_match_approved_table(policy):
    assert dict(policy.severities) == {
        "fall_detected": "HIGH",
        "altercation_suspected": "HIGH",
        "restricted_zone_entry": "LOW",
        "door_obstruction": "MEDIUM",
        "overcrowding": "MEDIUM",
        "occupancy_level_change": "LOW",
    }
    assert policy.overcrowding_escalates_at == 1.5
    assert policy.overcrowding_escalates_to == "HIGH"
    assert dict(policy.cooldowns_s) == {
        "fall_detected": 300.0,
        "altercation_suspected": 180.0,
        "overcrowding": 120.0,
        "occupancy_level_change": 120.0,
        "restricted_zone_entry": 600.0,
        "door_obstruction": 300.0,
    }
    assert policy.fight_escalation_window_s == 600.0
    assert policy.fight_escalation_severity == "CRITICAL"


def test_emittable_kinds_drift_guard():
    assert EMITTABLE_KINDS | RESERVED_KINDS == set(EVENT_TYPES)
    assert RESERVED_KINDS == {"person_down"}


def test_debounce_bridge_carries_policy_into_engine_format(policy):
    debounce = policy.debounce()
    assert isinstance(debounce, DebouncePolicy)
    assert debounce.fight_escalation_window_s == policy.fight_escalation_window_s
    assert debounce.fight_escalation_severity == "CRITICAL"
    assert dict(debounce.cooldowns_s) == dict(policy.cooldowns_s)


# --- resolver rules ------------------------------------------------------------


RESOLVER_CASES = [
    ("fall_detected", "fall_detected", "HIGH"),
    ("altercation_suspected", "altercation_suspected", "HIGH"),
    ("restricted_zone_entry", "restricted_zone_entry", "LOW"),
    ("door_obstruction", "door_obstruction", "MEDIUM"),
]


@pytest.mark.parametrize(
    ("kind", "event_type", "severity"),
    [
        *[(kind, etype, sev) for kind, etype, sev in RESOLVER_CASES],
        ("occ:crowded", "occupancy_level_change", "LOW"),
        ("occ:normal", "occupancy_level_change", "LOW"),
        ("occ:overcrowded", "overcrowding", "MEDIUM"),
        ("occ:overcrowded:1.4", "overcrowding", "MEDIUM"),
        ("occ:overcrowded:1.5", "overcrowding", "HIGH"),
        ("occ:overcrowded:2.0", "overcrowding", "HIGH"),
    ],
)
def test_resolver_rules(resolver, kind, event_type, severity):
    if kind.startswith("occ:"):
        parts = kind.split(":")
        ratio = float(parts[2]) if len(parts) > 2 else None
        row = occ(parts[1], ratio)
    else:
        row = EventRow(kind=kind, camera_id="C", zone="z", track_id=1, ts=1.0)
    assert resolver(row) == Resolution(event_type, severity)


def test_into_over_without_ratio_uses_base_severity(resolver):
    assert resolver(occ("overcrowded")) == Resolution("overcrowding", "MEDIUM")


def test_resolver_fail_fast_on_reserved_or_unknown_kind(resolver):
    with pytest.raises(ValueError, match="person_down"):
        resolver(EventRow(kind="person_down", camera_id="C", ts=1.0))
    with pytest.raises(ValueError, match="no severity configured"):
        resolver(EventRow(kind="panic_detected", camera_id="C", ts=1.0))


# --- the YAML-edit proof (runbook 6.2 done-when) -------------------------------


def _write_variant(tmp_path: Path, mutate) -> Path:
    raw = yaml.safe_load(CONFIG.read_text())
    mutate(raw)
    variant = tmp_path / "severity.yaml"
    variant.write_text(yaml.safe_dump(raw))
    return variant


def test_yaml_edit_changes_severity_zero_code_changes(tmp_path):
    def demote_fall(cfg: dict) -> None:
        cfg["severity"]["fall_detected"] = "LOW"

    variant_policy = load_severity_policy(_write_variant(tmp_path, demote_fall))
    variant_resolver = make_resolver(variant_policy)
    row = EventRow(kind="fall_detected", camera_id="C", track_id=1, ts=1.0)

    assert make_resolver(load_severity_policy(CONFIG))(row) == Resolution("fall_detected", "HIGH")
    assert variant_resolver(row) == Resolution("fall_detected", "LOW")


def test_yaml_edit_changes_ratio_threshold(tmp_path):
    variant_policy = load_severity_policy(
        _write_variant(tmp_path, lambda cfg: cfg["severity"].__setitem__(
            "overcrowding_ratio_escalation", {"at": 1.2, "to": "HIGH"}
        ))
    )
    variant_resolver = make_resolver(variant_policy)
    assert variant_resolver(occ("overcrowded", ratio=1.3)) == Resolution("overcrowding", "HIGH")


def test_removing_escalation_block_disables_it(tmp_path):
    def drop_escalation(cfg: dict) -> None:
        del cfg["severity"]["overcrowding_ratio_escalation"]

    variant = load_severity_policy(_write_variant(tmp_path, drop_escalation))
    assert make_resolver(variant)(occ("overcrowded", ratio=9.0)) == Resolution(
        "overcrowding", "MEDIUM"
    )


# --- strict fail-fast validation -----------------------------------------------


def test_malformed_yaml_clear_error(tmp_path):
    broken = tmp_path / "severity.yaml"
    broken.write_text("severity: [unclosed")
    with pytest.raises(SeverityConfigError, match="invalid YAML"):
        load_severity_policy(broken)


def test_unknown_severity_value_rejected(tmp_path):
    def bad_value(cfg: dict) -> None:
        cfg["severity"]["fall_detected"] = "EXTREME"

    with pytest.raises(SeverityConfigError, match="severity.fall_detected = 'EXTREME'"):
        load_severity_policy(_write_variant(tmp_path, bad_value))


def test_unknown_kind_typo_rejected_with_valid_list(tmp_path):
    def typo(cfg: dict) -> None:
        cfg["severity"]["fall_detectd"] = "HIGH"

    with pytest.raises(SeverityConfigError, match="unknown kind"):
        load_severity_policy(_write_variant(tmp_path, typo))


def test_reserved_kind_rejected_as_not_configurable(tmp_path):
    def reserved(cfg: dict) -> None:
        cfg["severity"]["person_down"] = "CRITICAL"

    with pytest.raises(SeverityConfigError, match="reserved"):
        load_severity_policy(_write_variant(tmp_path, reserved))


def test_missing_kind_rejected(tmp_path):
    def drop(cfg: dict) -> None:
        del cfg["severity"]["door_obstruction"]

    with pytest.raises(SeverityConfigError, match="missing kind"):
        load_severity_policy(_write_variant(tmp_path, drop))


def test_non_positive_cooldown_rejected(tmp_path):
    def zero(cfg: dict) -> None:
        cfg["debounce"]["cooldown_minutes"]["fall_detected"] = 0

    with pytest.raises(SeverityConfigError, match="positive number"):
        load_severity_policy(_write_variant(tmp_path, zero))


def test_bad_fight_escalation_severity_rejected(tmp_path):
    def bad(cfg: dict) -> None:
        cfg["debounce"]["fight_escalation"]["severity"] = "ULTRA"

    with pytest.raises(SeverityConfigError, match="fight_escalation.severity = 'ULTRA'"):
        load_severity_policy(_write_variant(tmp_path, bad))


def test_unexpected_top_level_key_rejected(tmp_path):
    def extra(cfg: dict) -> None:
        cfg["mystery_section"] = {}

    with pytest.raises(SeverityConfigError, match="top level"):
        load_severity_policy(_write_variant(tmp_path, extra))


# --- composition with the 6.1b engine ------------------------------------------


def test_real_policy_composes_with_event_engine(policy):
    from itertools import count

    counter = count(1)
    engine = EventEngine(
        builder=EnvelopeBuilder(
            source="/mobisentra/edge/BUS_102/BUS_102_CAM_04",
            model_versions={"detector": "x@1"},
            id_factory=lambda: f"00000000-0000-4000-8000-{next(counter):012d}",
        ),
        policy=policy.debounce(),
        resolver=make_resolver(policy),
    )
    rows = [
        EventRow(kind="fall_detected", camera_id="C", track_id=7, ts=10.0),
        occ("overcrowded", ratio=1.7),
        EventRow(
            kind="occupancy_level_change",
            camera_id="C",
            zone="bus_area",
            from_band="overcrowded",
            to_band="crowded",
            ts=11.0,
        ),
    ]
    emitted = engine.process(rows)
    assert [(row["data"]["event_type"], row["data"]["severity"]) for row in emitted] == [
        ("fall_detected", "HIGH"),
        ("overcrowding", "HIGH"),
        ("occupancy_level_change", "LOW"),
    ]
