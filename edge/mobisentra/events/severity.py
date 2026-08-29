"""Severity + debounce policy from ``configs/severity.yaml`` (Phase 6, 6.2).

Strict loader: unknown kinds, non-enum severities, missing or non-positive
values fail **at load** (startup), never mid-stream. Rule *structure* is
code (approved with the plan); only *values* are operator-editable:

- entering the ``overcrowded`` band → event_type ``overcrowding`` at
  ``severity.overcrowding``, escalated to ``overcrowding_ratio_escalation.to``
  when the row's ``ratio`` ≥ ``at`` (escalation optional — omit the block)
- every other band flip → ``occupancy_level_change`` at its configured severity
- all other kinds pass through at their configured severity
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

import yaml

from mobisentra.analytics.occupancy import OccupancyBand
from mobisentra.events.engine import DebouncePolicy, Resolution, ResolutionResolver
from mobisentra.events.envelope import EVENT_TYPES, SEVERITIES, Severity
from mobisentra.events.sink import EventRow

RESERVED_KINDS: Final[frozenset[str]] = frozenset({"person_down"})
EMITTABLE_KINDS: Final[frozenset[str]] = frozenset(set(EVENT_TYPES) - RESERVED_KINDS)

_OVERCROWDED_BAND: Final[str] = OccupancyBand.OVERCROWDED.value
_RATIO_ESCALATION: Final[str] = "overcrowding_ratio_escalation"


class SeverityConfigError(ValueError):
    """Raised at policy load for any malformed severity.yaml content."""


def _fail(path: Path, message: str) -> None:
    raise SeverityConfigError(f"{path}: {message}")


def _severity_value(path: Path, where: str, value: object) -> Severity:
    if value not in SEVERITIES:
        _fail(path, f"{where} = {value!r} not in {list(SEVERITIES)}")
    return str(value)


def _positive_number(path: Path, where: str, value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        _fail(path, f"{where} = {value!r} must be a positive number")
    return float(value)


@dataclass(frozen=True, slots=True)
class SeverityPolicy:
    """Immutable policy; severities keyed by event_type, cooldowns in seconds."""

    severities: Mapping[str, Severity]
    overcrowding_escalates_at: float | None
    overcrowding_escalates_to: Severity
    cooldowns_s: Mapping[str, float]
    fight_escalation_window_s: float
    fight_escalation_severity: Severity

    def debounce(self) -> DebouncePolicy:
        return DebouncePolicy(
            cooldowns_s=self.cooldowns_s,
            fight_escalation_window_s=self.fight_escalation_window_s,
            fight_escalation_severity=self.fight_escalation_severity,
        )


def _check_kind_keys(path: Path, section: str, keys: object) -> dict[str, object]:
    if not isinstance(keys, dict):
        _fail(path, f"{section} must be a mapping of kind → value")
    unknown = set(keys) - EMITTABLE_KINDS
    if unknown:
        reserved = sorted(unknown & RESERVED_KINDS)
        typos = sorted(unknown - RESERVED_KINDS)
        details = []
        if typos:
            details.append(f"unknown kind(s) {typos}; valid: {sorted(EMITTABLE_KINDS)}")
        if reserved:
            details.append(f"reserved kind(s) {reserved} are not configurable yet")
        _fail(path, f"{section}: {'; '.join(details)}")
    missing = EMITTABLE_KINDS - set(keys)
    if missing:
        _fail(path, f"{section}: missing kind(s) {sorted(missing)}")
    return dict(keys)


def _load_escalation(path: Path, raw: dict[str, object]) -> tuple[float | None, Severity]:
    """Read (and consume) the optional ratio-escalation block so the
    remaining ``severity:`` keys are exactly the kind keys."""
    if _RATIO_ESCALATION not in raw:
        return None, "HIGH"
    block = raw.pop(_RATIO_ESCALATION)
    if not isinstance(block, dict) or set(block) != {"at", "to"}:
        _fail(path, f"severity.{_RATIO_ESCALATION} must be exactly {{at, to}}")
    at = _positive_number(path, f"severity.{_RATIO_ESCALATION}.at", block["at"])
    to = _severity_value(path, f"severity.{_RATIO_ESCALATION}.to", block["to"])
    return at, to


def load_severity_policy(path: Path) -> SeverityPolicy:
    """Parse + strictly validate severity.yaml → immutable policy."""
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        _fail(path, f"invalid YAML: {exc}")
    if not isinstance(raw, dict) or set(raw) != {"severity", "debounce"}:
        _fail(path, "top level must be exactly 'severity' + 'debounce'")

    severity_raw = raw["severity"]
    if not isinstance(severity_raw, dict):
        _fail(path, "severity must be a mapping")
    escalates_at, escalates_to = _load_escalation(path, severity_raw)
    kind_entries = _check_kind_keys(path, "severity", severity_raw)
    severities = {
        kind: _severity_value(path, f"severity.{kind}", value)
        for kind, value in kind_entries.items()
    }

    debounce_raw = raw["debounce"]
    if not isinstance(debounce_raw, dict) or set(debounce_raw) != {
        "cooldown_minutes",
        "fight_escalation",
    }:
        _fail(path, "debounce must be exactly cooldown_minutes + fight_escalation")
    cooldown_raw = _check_kind_keys(
        path, "debounce.cooldown_minutes", debounce_raw["cooldown_minutes"]
    )
    cooldowns_s = {
        kind: 60.0 * _positive_number(path, f"debounce.cooldown_minutes.{kind}", minutes)
        for kind, minutes in cooldown_raw.items()
    }

    fight_raw = debounce_raw["fight_escalation"]
    if not isinstance(fight_raw, dict) or set(fight_raw) != {"window_minutes", "severity"}:
        _fail(path, "debounce.fight_escalation must be exactly window_minutes + severity")
    window_s = 60.0 * _positive_number(
        path, "debounce.fight_escalation.window_minutes", fight_raw["window_minutes"]
    )
    fight_severity = _severity_value(
        path, "debounce.fight_escalation.severity", fight_raw["severity"]
    )

    return SeverityPolicy(
        severities=MappingProxyType(severities),
        overcrowding_escalates_at=escalates_at,
        overcrowding_escalates_to=escalates_to,
        cooldowns_s=MappingProxyType(cooldowns_s),
        fight_escalation_window_s=window_s,
        fight_escalation_severity=fight_severity,
    )


def make_resolver(policy: SeverityPolicy) -> ResolutionResolver:
    """Build the row → Resolution mapper the engine consumes (Step 6.1b)."""

    def resolve(row: EventRow) -> Resolution:
        kind = str(row.get("kind", ""))
        if kind == "occupancy_level_change":
            if row.get("to_band") == _OVERCROWDED_BAND:
                severity = policy.severities["overcrowding"]
                escalates_at = policy.overcrowding_escalates_at
                ratio = row.get("ratio")
                if (
                    escalates_at is not None
                    and isinstance(ratio, (int, float))
                    and ratio >= escalates_at
                ):
                    severity = policy.overcrowding_escalates_to
                return Resolution("overcrowding", severity)
            return Resolution(
                "occupancy_level_change", policy.severities["occupancy_level_change"]
            )
        if kind not in policy.severities:
            raise ValueError(
                f"no severity configured for kind {kind!r} (person_down is reserved; "
                "every emittable kind must be in severity.yaml)"
            )
        return Resolution(kind, policy.severities[kind])

    return resolve
