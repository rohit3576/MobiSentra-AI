"""Event engine — debounce, escalation, emission (Phase 6, Step 6.1b).

Pure logic: candidate rows in → CloudEvents envelopes + suppression counts
out. No I/O, no wall-clock — **every window is measured on the row's stream
``ts``** (replay-safe: benchmarks running 6× realtime get correct debounce
behavior; production streams get wall-time semantics for free because
``capture_ts`` ≈ now).

Layering: severity + kind mapping are injected (``ResolutionResolver``);
Step 6.2 builds the resolver from ``configs/severity.yaml``. This module
owns only *state* semantics:

- **Cooldown** — max 1 emitted alert per ``(camera, event_type, subject)``
  per ``cooldowns_s[event_type]``. Subjects: track (fall), normalized pair
  (fight), (zone, track) (dwell kinds — a second person is a second
  incident), zone (occupancy kinds). Both occupancy kinds
  (``occupancy_level_change`` and the ``overcrowding`` override) share one
  **family key per zone** — the escalation that arms the throttle and the
  de-escalation that re-arms it resolve to different kinds, so kind-keyed
  state would never pair up. Suppressed rows are counted per key
  (telemetry); suppression never extends a window (``last_fire`` updates on
  emission only).
- **Occupancy de-escalation is exempt + re-arms** — a band-improving
  ``occupancy_level_change`` is never blocked by an escalation cooldown and
  clears the key state: the situation resolved, a new escalation into the
  band is a fresh incident.
- **Fight re-fire escalation** (the pre-backend "confirmed altercation"
  proxy) — for a pair that fired at ``t0``: Δt ≤ cooldown → suppressed
  spam; cooldown < Δt ≤ escalation window → emitted at
  ``fight_escalation_severity``; Δt > window → fresh incident at the
  resolver's severity.

Assumes monotonically non-decreasing ``ts`` per camera (stream guarantee).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from mobisentra.analytics.occupancy import OccupancyBand
from mobisentra.events.envelope import EnvelopeBuilder, Severity
from mobisentra.events.sink import EventRow

FIGHT_KIND: str = "altercation_suspected"
DWELL_KINDS: frozenset[str] = frozenset({"restricted_zone_entry", "door_obstruction"})
OCCUPANCY_KINDS: frozenset[str] = frozenset({"occupancy_level_change", "overcrowding"})

_BAND_RANK: dict[str, int] = {band.value: rank for rank, band in enumerate(OccupancyBand)}

Subject = tuple[int, ...] | tuple[str, int] | tuple[str] | tuple[()]


@dataclass(frozen=True, slots=True)
class Resolution:
    """What the severity mapper (6.2) decided for a row."""

    event_type: str
    severity: Severity


ResolutionResolver = Callable[[EventRow], Resolution]


@dataclass(frozen=True, slots=True)
class DebouncePolicy:
    """Debounce knobs (Step 6.2 loads these from ``configs/severity.yaml``)."""

    cooldowns_s: Mapping[str, float]
    fight_escalation_window_s: float
    fight_escalation_severity: Severity


@dataclass(slots=True)
class EngineResult:
    """One ``process`` batch: emitted envelopes + per-key suppression counts."""

    emitted: list[dict[str, object]] = field(default_factory=list)
    suppressed: dict[str, int] = field(default_factory=dict)


def _subject(event_type: str, row: EventRow) -> Subject:
    if event_type == FIGHT_KIND and "track_a" in row and "track_b" in row:
        a, b = int(row["track_a"]), int(row["track_b"])  # order-proof pair key
        return (min(a, b), max(a, b))
    if event_type in DWELL_KINDS and "zone" in row and "track_id" in row:
        return (str(row["zone"]), int(row["track_id"]))
    if event_type == "fall_detected" and "track_id" in row:
        return (int(row["track_id"]),)
    if event_type in OCCUPANCY_KINDS and "zone" in row:
        return (str(row["zone"]),)
    return ()


def _key(family: str, subject: Subject) -> str:
    return "/".join(str(part) for part in (family, *subject))


def _throttle_key(event_type: str, row: EventRow) -> str:
    """Cooldown key: kind + subject, except occupancy kinds which share one
    family key per zone (escalation arms it, de-escalation re-arms it)."""
    family = "occupancy" if event_type in OCCUPANCY_KINDS else event_type
    return _key(family, _subject(event_type, row))


def _is_deescalation(row: EventRow) -> bool:
    """Band-improving occupancy flip (``from_band`` worse than ``to_band``);
    rows without both bands are never de-escalations."""
    if "from_band" not in row or "to_band" not in row:
        return False
    try:
        return _BAND_RANK[str(row["to_band"])] < _BAND_RANK[str(row["from_band"])]
    except KeyError:
        return False


class EventEngine:
    """Per-camera debounce layer over :class:`EnvelopeBuilder`."""

    def __init__(
        self,
        *,
        builder: EnvelopeBuilder,
        policy: DebouncePolicy,
        resolver: ResolutionResolver,
    ) -> None:
        self._builder = builder
        self._policy = policy
        self._resolver = resolver
        self._last_fire: dict[str, float] = {}
        self._result = EngineResult()

    @property
    def suppressed(self) -> dict[str, int]:
        """Suppression counts since construction / last ``reset``."""
        return self._result.suppressed

    def reset(self) -> None:
        """Clear cooldown + escalation state (tests, pipeline restarts)."""
        self._last_fire.clear()
        self._result = EngineResult()

    def process(self, rows: Iterable[EventRow]) -> list[dict[str, object]]:
        """A batch of candidate rows → the envelopes that survived debounce."""
        emitted: list[dict[str, object]] = []
        for row in rows:
            resolution = self._resolver(row)
            if resolution.event_type not in self._policy.cooldowns_s:
                raise ValueError(
                    f"no debounce policy for event type {resolution.event_type!r} "
                    f"(severity.yaml must cover every emittable kind)"
                )
            ts = float(row["ts"])
            key = _throttle_key(resolution.event_type, row)
            if resolution.event_type in OCCUPANCY_KINDS and _is_deescalation(row):
                # exempt + re-arm: resolution events always pass and clear state
                self._last_fire.pop(key, None)
                emitted.append(
                    self._builder.build(
                        row,
                        severity=resolution.severity,
                        event_type=resolution.event_type,
                    )
                )
                continue
            last = self._last_fire.get(key)
            cooldown = float(self._policy.cooldowns_s[resolution.event_type])
            if last is not None and ts - last < cooldown:
                self._result.suppressed[key] = self._result.suppressed.get(key, 0) + 1
                continue
            severity = resolution.severity
            if (
                resolution.event_type == FIGHT_KIND
                and last is not None
                and ts - last < float(self._policy.fight_escalation_window_s)
            ):
                severity = self._policy.fight_escalation_severity
            self._last_fire[key] = ts
            emitted.append(
                self._builder.build(row, severity=severity, event_type=resolution.event_type)
            )
        self._result.emitted.extend(emitted)
        return emitted
