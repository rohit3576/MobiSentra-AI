"""Fall rule cascade v1 (Phase 4, Step 4.3).

Pure logic — no cv, no I/O. Per-track state machine over the Step 4.2
features:

    WATCHING ──trigger──▶ CONFIRMING ──still down ≥ T──▶ FIRED (one event)
       ▲                     │                              │
       └────recovery─────────┘─────────recovery (re-arm)────┘

Trigger (both required): hip velocity ≥ ``velocity_body_heights_per_s``
downward AND torso angle < ``torso_angle_deg``. Head-near-hip is optional
(raises confidence, never blocks) — occlusion must not suppress a fall
seen on good core features. Recovery needs POSITIVE evidence (torso angle
> ``recovery_angle_deg``): unknown features during the confirm window mean
"still down", not "fine" (bus1 lesson). Velocity is normalized to
body-heights per second (mean bbox height of the last two samples) so
thresholds are resolution- and distance-independent. Event kind is the
schemas v0 ``fall_detected`` string.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from mobisentra.analytics.fall_features import (
    FallFeatures,
    compute_fall_features,
    head_hip_vertical_offset,
)
from mobisentra.vision.track_history import PoseSample

EVENT_KIND: Final = "fall_detected"


@dataclass(frozen=True, slots=True)
class FallConfig:
    velocity_body_heights_per_s: float = 0.75
    torso_angle_deg: float = 35.0
    head_hip_vertical_max: float = 0.25
    recovery_angle_deg: float = 55.0
    confirm_seconds: float = 3.0


@dataclass(frozen=True, slots=True)
class FallCandidate:
    kind: str
    track_id: int
    ts: float
    trigger_ts: float
    confidence: float


class _Phase(Enum):
    WATCHING = auto()
    CONFIRMING = auto()
    FIRED = auto()


@dataclass(slots=True)
class _TrackState:
    """Mutable per-track cascade state — exists to survive across frames."""

    phase: _Phase = _Phase.WATCHING
    trigger_ts: float = 0.0
    confidence: float = 0.0


class FallDetector:
    """One instance per camera; ``update`` once per analyzed frame per track."""

    def __init__(self, config: FallConfig | None = None) -> None:
        self._config = config or FallConfig()
        self._states: dict[int, _TrackState] = {}

    def forget(self, track_id: int) -> None:
        self._states.pop(track_id, None)

    def update(self, track_id: int, samples: Sequence[PoseSample]) -> list[FallCandidate]:
        if not samples:
            self.forget(track_id)
            return []
        state = self._states.setdefault(track_id, _TrackState())
        features = compute_fall_features(samples)
        latest = samples[-1]

        match state.phase:
            case _Phase.WATCHING:
                if self._triggers(features, samples):
                    state.phase = _Phase.CONFIRMING
                    state.trigger_ts = latest.ts
                    state.confidence = self._confidence(features, samples)
            case _Phase.CONFIRMING:
                if self._recovered(features):
                    state.phase = _Phase.WATCHING
                elif latest.ts - state.trigger_ts >= self._config.confirm_seconds:
                    state.phase = _Phase.FIRED
                    return [
                        FallCandidate(
                            kind=EVENT_KIND,
                            track_id=track_id,
                            ts=latest.ts,
                            trigger_ts=state.trigger_ts,
                            confidence=state.confidence,
                        )
                    ]
            case _Phase.FIRED:
                if self._recovered(features):
                    state.phase = _Phase.WATCHING
        return []

    def _body_heights_per_s(
        self, features: FallFeatures, samples: Sequence[PoseSample]
    ) -> float | None:
        if features.hip_vertical_velocity is None or len(samples) < 2:
            return None
        heights = [s.bbox[3] - s.bbox[1] for s in samples[-2:]]
        mean_height = sum(heights) / len(heights)
        if mean_height <= 0.0:
            return None
        return features.hip_vertical_velocity / mean_height

    def _triggers(self, features: FallFeatures, samples: Sequence[PoseSample]) -> bool:
        velocity = self._body_heights_per_s(features, samples)
        if velocity is None or velocity < self._config.velocity_body_heights_per_s:
            return False
        return (
            features.torso_angle_deg is not None
            and features.torso_angle_deg < self._config.torso_angle_deg
        )

    def _recovered(self, features: FallFeatures) -> bool:
        return (
            features.torso_angle_deg is not None
            and features.torso_angle_deg > self._config.recovery_angle_deg
        )

    def _confidence(self, features: FallFeatures, samples: Sequence[PoseSample]) -> float:
        velocity = self._body_heights_per_s(features, samples)
        score = 0.5
        if velocity is not None and velocity >= 2.0 * self._config.velocity_body_heights_per_s:
            score += 0.25
        offset = head_hip_vertical_offset(samples[-1])
        if offset is not None and offset <= self._config.head_hip_vertical_max:
            score += 0.25
        return min(score, 1.0)
