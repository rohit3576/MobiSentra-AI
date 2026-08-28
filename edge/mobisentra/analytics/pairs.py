"""Sustained-proximity pair finding (Phase 5, Step 5.2).

Pure logic — no cv, no I/O, no model. Feeds Step 5.3's fusion: a pair of
tracks that stays overlapping-or-near for ``sustain_frames`` analyzed
frames becomes a candidate interaction pair, and its union box is the crop
source the ActionScorer scores. Proximity is scale-invariant: IoU ≥
``iou_min`` OR center distance ≤ ``center_distance_diags`` × the pair's
mean box diagonal — "nearby" must mean the same thing for two people at
the front and the back of a bus.

Gap tolerance (``gap_frames``): occlusion and detector flicker make
proximity flicker at 5 fps — a missed-proximity frame does not reset the
run (occupancy-hysteresis philosophy, Phase 3), but ``gap_frames + 1``
consecutive misses do. Emission returns every ACTIVE pair (run ≥
``sustain_frames``, both boxes present and proximate in THIS frame) so the
consumer always has a current union box to crop — pair formation is simply
the first frame it appears in the output. ``forget`` mirrors FallDetector:
the engine's purge loop drops pairs whose tracks die (Phase 4 leak-fix
precedent — pair state must not outlive its tracks).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

Box = tuple[float, float, float, float]  # xyxy pixels

EVENT_KIND: Final = "interaction_pair"  # schemas-v0 candidate kind (consumed by 5.3)


@dataclass(frozen=True, slots=True)
class PairConfig:
    iou_min: float = 0.08
    center_distance_diags: float = 0.9
    sustain_frames: int = 5
    gap_frames: int = 2


@dataclass(frozen=True, slots=True)
class InteractionPair:
    kind: str
    track_a: int
    track_b: int
    ts: float
    union_box: Box
    run_frames: int
    first_proximate_ts: float


@dataclass(slots=True)
class _PairState:
    run: int = 0
    missed: int = 0
    first_proximate_ts: float = 0.0


def _center(box: Box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def iou(a: Box, b: Box) -> float:
    inter_w = min(a[2], b[2]) - max(a[0], b[0])
    inter_h = min(a[3], b[3]) - max(a[1], b[1])
    if inter_w <= 0.0 or inter_h <= 0.0:
        return 0.0
    inter = inter_w * inter_h
    total = _area(a) + _area(b) - inter
    return inter / total if total > 0.0 else 0.0


def _center_distance_diags(a: Box, b: Box) -> float:
    """Center distance in units of the pair's mean box diagonal (a square's
    diagonal is side × √2; sqrt(area) is the mean side regardless of aspect)."""
    mean_side = (_area(a) ** 0.5 + _area(b) ** 0.5) / 2.0
    if mean_side <= 0.0:
        return float("inf")
    ax, ay = _center(a)
    bx, by = _center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 / (mean_side * 2.0**0.5)


def _proximate(a: Box, b: Box, config: PairConfig) -> bool:
    if _area(a) <= 0.0 or _area(b) <= 0.0:
        return False
    return (
        iou(a, b) >= config.iou_min
        or _center_distance_diags(a, b) <= config.center_distance_diags
    )


class PairFinder:
    """One instance per camera; ``update`` once per analyzed frame with the
    frame's track boxes. Returns the active candidate pairs for THIS frame."""

    def __init__(self, config: PairConfig | None = None) -> None:
        self._config = config or PairConfig()
        self._states: dict[tuple[int, int], _PairState] = {}

    def forget(self, track_id: int) -> None:
        stale = [key for key in self._states if track_id in key]
        for key in stale:
            del self._states[key]

    def active_pair_ids(self) -> list[tuple[int, int]]:
        """Pairs currently at/over the sustain threshold — telemetry."""
        return [key for key, s in self._states.items() if s.run >= self._config.sustain_frames]

    def update(self, ts: float, boxes: Mapping[int, Box]) -> list[InteractionPair]:
        config = self._config
        seen: set[tuple[int, int]] = set()
        track_ids = sorted(boxes)
        for i, a in enumerate(track_ids):
            for b in track_ids[i + 1 :]:
                key = (a, b)
                if not _proximate(boxes[a], boxes[b], config):
                    continue
                seen.add(key)
                state = self._states.setdefault(key, _PairState())
                if state.run == 0:
                    state.first_proximate_ts = ts
                state.run += 1
                state.missed = 0

        missed_keys = set(self._states) - seen
        for key in missed_keys:
            state = self._states[key]
            if key[0] not in boxes and key[1] not in boxes:
                del self._states[key]
                continue
            state.missed += 1
            if state.missed > config.gap_frames:
                del self._states[key]

        return [
            InteractionPair(
                kind=EVENT_KIND,
                track_a=a,
                track_b=b,
                ts=ts,
                union_box=_union(boxes[a], boxes[b]),
                run_frames=self._states[(a, b)].run,
                first_proximate_ts=self._states[(a, b)].first_proximate_ts,
            )
            for a, b in sorted(seen)
            if self._states[(a, b)].run >= config.sustain_frames
        ]
