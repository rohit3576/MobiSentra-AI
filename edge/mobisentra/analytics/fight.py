"""Altercation signal fusion (Phase 5, Step 5.3).

Pure logic — no cv, no I/O, no model. The action model NEVER alerts alone
(Wollongong lesson: 23% field FP rate): a fight candidate requires ALL
four signals holding simultaneously for ``sustain_s``:

    1. proximity sustained   — the pair is an ACTIVE PairFinder candidate
                              (Step 5.2 owns run/gap semantics)
    2. action score ≥ S      — ActionScorer P(Fight) on the pair's union crop
    3. rapid relative motion — relative center speed (box-diagonal units per
                              second, scale-invariant like the fall cascade's
                              body-heights normalization) peaking within the
                              window — strikes are bursts, so peak not mean
    4. repeated contact      — box-intersection oscillation: ≥
                              ``min_contact_onsets`` IoU onsets across
                              ``contact_iou`` within the window (clinch-break-
                              clinch), OR one contact held ≥
                              ``sustained_contact_s`` (grappling)

Re-arm needs POSITIVE de-escalation (action score below S, or the pair
gone) — one fight stays one event while signals persist. Severity mapping
and debouncing are Phase 6's, not ours. Event kind is the schemas-v0
``altercation_suspected`` string ("suspected": fusion suspects, the Phase 6
engine confirms).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final

from mobisentra.analytics.pairs import Box, InteractionPair, iou

EVENT_KIND: Final = "altercation_suspected"


@dataclass(frozen=True, slots=True)
class FightConfig:
    action_score_min: float = 0.6
    rel_motion_diags_per_s: float = 0.6
    contact_iou: float = 0.05
    min_contact_onsets: int = 2
    sustained_contact_s: float = 1.5
    window_s: float = 3.0
    sustain_s: float = 0.6


@dataclass(frozen=True, slots=True)
class FightCandidate:
    kind: str
    track_a: int
    track_b: int
    ts: float
    trigger_ts: float
    confidence: float
    action_score: float


class _Phase(Enum):
    WATCHING = auto()
    FIRED = auto()


@dataclass(slots=True)
class _PairState:
    phase: _Phase = _Phase.WATCHING
    hold_since: float | None = None
    trigger_ts: float = 0.0
    last_centers: tuple[tuple[float, float], tuple[float, float]] | None = None
    last_ts: float = 0.0
    samples: deque[tuple[float, float, float]] = field(default_factory=deque)


def _center(box: Box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _mean_diag(a: Box, b: Box) -> float:
    side = (((a[2] - a[0]) * (a[3] - a[1])) ** 0.5 + ((b[2] - b[0]) * (b[3] - b[1])) ** 0.5) / 2.0
    return side * 2.0**0.5


def _contact_onsets(samples: Sequence[tuple[float, float, float]], contact_iou: float) -> int:
    """Rising crossings of ``contact_iou``; a window that STARTS in contact
    counts that contact as one onset (it began before the window)."""
    onsets = 0
    prev: float | None = None
    for _, overlap, _ in samples:
        if prev is not None and prev < contact_iou <= overlap:
            onsets += 1
        prev = overlap
    if samples and samples[0][1] >= contact_iou:
        onsets += 1
    return onsets


def _sustained_contact(samples: Sequence[tuple[float, float, float]], contact_iou: float) -> float:
    """Longest continuous contact span anywhere in the window."""
    best = 0.0
    start: float | None = None
    for ts, overlap, _ in samples:
        if overlap >= contact_iou:
            if start is None:
                start = ts
            best = max(best, ts - start)
        else:
            start = None
    return best


class FightDetector:
    """One instance per camera; ``update`` once per analyzed frame with the
    active pairs, their action scores, and the frame's track boxes."""

    def __init__(self, config: FightConfig | None = None) -> None:
        self._config = config or FightConfig()
        self._states: dict[tuple[int, int], _PairState] = {}

    def forget(self, track_id: int) -> None:
        stale = [key for key in self._states if track_id in key]
        for key in stale:
            del self._states[key]

    def forget_pair(self, pair: tuple[int, int]) -> None:
        self._states.pop(pair, None)

    def pending_pair_ids(self) -> list[tuple[int, int]]:
        return [key for key, s in self._states.items() if s.phase is _Phase.FIRED]

    def update(
        self,
        ts: float,
        pairs: Sequence[InteractionPair],
        action_scores: Mapping[tuple[int, int], float],
        boxes: Mapping[int, Box],
    ) -> list[FightCandidate]:
        config = self._config
        active_keys = set()
        for pair in pairs:
            key = (pair.track_a, pair.track_b)
            active_keys.add(key)
            if pair.track_a not in boxes or pair.track_b not in boxes:
                continue
            box_a, box_b = boxes[pair.track_a], boxes[pair.track_b]
            state = self._states.setdefault(key, _PairState())
            self._record(state, ts, box_a, box_b)
            score = action_scores.get(key, 0.0)
            if state.phase is _Phase.FIRED:
                if score < config.action_score_min:
                    state.phase = _Phase.WATCHING
                    state.hold_since = None
                continue
            if score < config.action_score_min:
                state.hold_since = None
                continue
            if not self._motion_and_contact(state, config):
                state.hold_since = None
                continue
            if state.hold_since is None:
                state.hold_since = ts
            if ts - state.hold_since >= config.sustain_s:
                state.phase = _Phase.FIRED
                state.trigger_ts = state.hold_since
                return [
                    FightCandidate(
                        kind=EVENT_KIND,
                        track_a=pair.track_a,
                        track_b=pair.track_b,
                        ts=ts,
                        trigger_ts=state.hold_since,
                        confidence=self._confidence(state, config, score),
                        action_score=score,
                    )
                ]

        for key in set(self._states) - active_keys:
            self._states.pop(key, None)
        return []

    def _record(self, state: _PairState, ts: float, box_a: Box, box_b: Box) -> None:
        config = self._config
        center_a, center_b = _center(box_a), _center(box_b)
        rel_speed = 0.0
        if state.last_centers is not None and ts > state.last_ts:
            (pa, pb) = state.last_centers
            moved = ((center_a[0] - pa[0]) ** 2 + (center_a[1] - pa[1]) ** 2) ** 0.5
            moved += ((center_b[0] - pb[0]) ** 2 + (center_b[1] - pb[1]) ** 2) ** 0.5
            diag = _mean_diag(box_a, box_b)
            if diag > 0.0:
                rel_speed = moved / diag / (ts - state.last_ts)
        state.samples.append((ts, iou(box_a, box_b), rel_speed))
        while state.samples and state.samples[0][0] < ts - config.window_s:
            state.samples.popleft()
        state.last_centers = (center_a, center_b)
        state.last_ts = ts

    def _motion_and_contact(self, state: _PairState, config: FightConfig) -> bool:
        if not state.samples:
            return False
        peak_speed = max(speed for _, _, speed in state.samples)
        if peak_speed < config.rel_motion_diags_per_s:
            return False
        onsets = _contact_onsets(state.samples, config.contact_iou)
        if onsets >= config.min_contact_onsets:
            return True
        return _sustained_contact(state.samples, config.contact_iou) >= config.sustained_contact_s

    def _confidence(self, state: _PairState, config: FightConfig, score: float) -> float:
        peak = max((speed for _, _, speed in state.samples), default=0.0)
        onsets = _contact_onsets(state.samples, config.contact_iou)
        strong_contact = onsets >= config.min_contact_onsets + 1 or onsets <= 1
        confidence = 0.5
        if peak >= 2.0 * config.rel_motion_diags_per_s:
            confidence += 0.15
        if strong_contact:
            confidence += 0.2
        if score >= 0.8:
            confidence += 0.15
        return min(confidence, 1.0)
