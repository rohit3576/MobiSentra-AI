"""Fall rule cascade v1 (Phase 4, Step 4.3; ID-switch tolerance 4.4).

Pure logic — no cv, no I/O. Per-track state machine over the Step 4.2
features:

    WATCHING ──trigger──▶ CONFIRMING ──still down ≥ T──▶ FIRED (one event)
       ▲                     │                              │
       └────recovery─────────┘─────────recovery (re-arm)────┘

Trigger (either path, both need an upright sample within
``upright_within_s`` — UR Fall ADL lesson: pose jitter on a body that has
been lying for seconds fakes ±5 BH/s hip velocity): **(a)** hip velocity
≥ ``velocity_body_heights_per_s`` downward AND torso angle <
``torso_angle_deg``, or **(b)** hip velocity ≥
``velocity_high_bar_bh_s`` (near-free-fall; a fast sit peaks ≈1.0–1.5 —
UR Fall knee-drop falls keep the torso at 60–80° while dropping at
2.5+). Head-near-hip is optional
(raises confidence, never blocks) — occlusion must not suppress a fall
seen on good core features. Recovery needs POSITIVE evidence (torso angle
> ``recovery_angle_deg``): unknown features during the confirm window mean
"still down", not "fine" (bus1 lesson). Velocity is normalized to
body-heights per second (mean bbox height of the last two samples) so
thresholds are resolution- and distance-independent. Event kind is the
schemas v0 ``fall_detected`` string.

ID-switch tolerance (first UR Fall run, 2026-08-27): trackers re-label a
person mid-collapse (the trigger track dies frozen in the lying pose, a new
id inherits the body, and duplicate upright ids of the same person sit in
the trigger area). Three consequences. Confirm-window elapsed time uses
the CALLER's frame clock (``now_ts``) — a frozen history still confirms.
Triggers only evaluate FRESH samples (stale vs frame clock = no re-arm on
old collapse evidence). And recovery via other tracks needs SUSTAINED
upright evidence (``_OTHER_RECOVERY_SUSTAIN_S``) inside the expanded
trigger bbox — one noisy lying frame past the recovery angle must not
read as the person getting up. Occlusion of every witness still means
"down" (Step 4.3 rule, unchanged).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from mobisentra.analytics.fall_features import (
    FallFeatures,
    compute_fall_features,
    head_hip_vertical_offset,
    torso_angle,
)
from mobisentra.vision.track_history import PoseSample

EVENT_KIND: Final = "fall_detected"
_AREA_GROWTH: Final = 0.5  # trigger bbox grows 50% per side to match others
_TRIGGER_STALE_S: Final = 0.5  # latest sample older than this vs frame clock = no trigger
_OTHER_RECOVERY_SUSTAIN_S: Final = 0.75  # other-track upright must persist this long
_RECOVERY_HIP_MARGIN: Final = 0.15  # body-heights; hips within this of standing level = up
_HIGH_BAR_PAIRS: Final = 2  # consecutive sample-pairs for the near-free-fall path


@dataclass(frozen=True, slots=True)
class FallConfig:
    velocity_body_heights_per_s: float = 0.75
    velocity_high_bar_bh_s: float = 2.0
    torso_angle_deg: float = 35.0
    head_hip_vertical_max: float = 0.25
    recovery_angle_deg: float = 55.0
    confirm_seconds: float = 3.0
    upright_within_s: float = 1.0


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
    trigger_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    hip_ref_y: float | None = None
    hip_ref_body_h: float = 0.0


def _grow(
    bbox: tuple[float, float, float, float], growth: float
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    width, height = x2 - x1, y2 - y1
    return (
        x1 - width * growth,
        y1 - height * growth,
        x2 + width * growth,
        y2 + height * growth,
    )


def _hip_midpoint(sample: PoseSample) -> tuple[float, float] | None:
    hips = [sample.keypoints[index] for index in (11, 12) if sample.keypoints[index][2] > 0.0]
    if not hips:
        return None
    return (sum(x for x, _, _ in hips) / len(hips), sum(y for _, y, _ in hips) / len(hips))


def _inside(point: tuple[float, float], bbox: tuple[float, float, float, float]) -> bool:
    x1, y1, x2, y2 = bbox
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


class FallDetector:
    """One instance per camera; ``update`` once per analyzed frame per track."""

    def __init__(self, config: FallConfig | None = None) -> None:
        self._config = config or FallConfig()
        self._states: dict[int, _TrackState] = {}

    def forget(self, track_id: int) -> None:
        self._states.pop(track_id, None)

    def pending_track_ids(self) -> list[int]:
        """Tracks mid-cascade (CONFIRMING or FIRED) — benchmark telemetry."""
        return [
            track_id
            for track_id, state in self._states.items()
            if state.phase in (_Phase.CONFIRMING, _Phase.FIRED)
        ]

    def update(
        self,
        track_id: int,
        samples: Sequence[PoseSample],
        *,
        now_ts: float | None = None,
        others: Mapping[int, Sequence[PoseSample]] | None = None,
    ) -> list[FallCandidate]:
        if not samples:
            self.forget(track_id)
            return []
        state = self._states.setdefault(track_id, _TrackState())
        features = compute_fall_features(samples)
        latest = samples[-1]
        now = latest.ts if now_ts is None else now_ts

        match state.phase:
            case _Phase.WATCHING:
                if now - latest.ts <= _TRIGGER_STALE_S and self._triggers(features, samples):
                    state.phase = _Phase.CONFIRMING
                    state.trigger_ts = latest.ts
                    state.trigger_bbox = latest.bbox
                    state.confidence = self._confidence(features, samples)
                    reference = self._standing_reference(samples)
                    if reference is not None:
                        state.hip_ref_y, state.hip_ref_body_h = reference
            case _Phase.CONFIRMING:
                if self._recovered(features, state, samples, others):
                    state.phase = _Phase.WATCHING
                elif now - state.trigger_ts >= self._config.confirm_seconds:
                    state.phase = _Phase.FIRED
                    return [
                        FallCandidate(
                            kind=EVENT_KIND,
                            track_id=track_id,
                            ts=now,
                            trigger_ts=state.trigger_ts,
                            confidence=state.confidence,
                        )
                    ]
            case _Phase.FIRED:
                if self._recovered(features, state, samples, others):
                    state.phase = _Phase.WATCHING
        return []

    def _upright_sustained(self, samples: Sequence[PoseSample]) -> bool:
        """Upright continuously for ≥ sustain, judged on the track's OWN
        timeline ending at its last observation (None torso breaks the run —
        occlusion is not recovery evidence)."""
        span_start = samples[0].ts
        for sample in reversed(samples):
            angle = torso_angle(sample)
            if angle is None or angle <= self._config.recovery_angle_deg:
                span_start = sample.ts
                break
        return samples[-1].ts - span_start >= _OTHER_RECOVERY_SUSTAIN_S

    def _other_recovered(
        self, state: _TrackState, others: Mapping[int, Sequence[PoseSample]] | None
    ) -> bool:
        """A successor track got the person up: BORN AFTER the trigger (a
        pre-existing standing duplicate fails here — UR Fall fall-23 lesson:
        the duplicate lives past the trigger and used to read as recovery),
        hips inside the trigger area, upright ≥ sustain on its own timeline,
        observed after trigger + sustain."""
        if not others:
            return False
        area = _grow(state.trigger_bbox, _AREA_GROWTH)
        for samples in others.values():
            if not samples or samples[0].ts < state.trigger_ts:
                continue
            if samples[-1].ts < state.trigger_ts + _OTHER_RECOVERY_SUSTAIN_S:
                continue
            hip = _hip_midpoint(samples[-1])
            if hip is None or not _inside(hip, area):
                continue
            if self._upright_sustained(samples):
                return True
        return False

    def _recovered(
        self,
        features: FallFeatures,
        state: _TrackState,
        samples: Sequence[PoseSample],
        others: Mapping[int, Sequence[PoseSample]] | None,
    ) -> bool:
        return self._own_recovered(features, state, samples) or self._other_recovered(state, others)

    def _own_recovered(
        self, features: FallFeatures, state: _TrackState, samples: Sequence[PoseSample]
    ) -> bool:
        """Upright torso is NOT enough: a knee-drop fall ends kneeling with a
        vertical torso but hips ~0.3 body-heights below standing. Recovery =
        upright torso AND hips back near the pre-fall standing level (knee-drop
        lesson, UR Fall fall-25, 2026-08-27). Occluded hips = unknown, not
        recovered (Step 4.3 rule)."""
        if not (
            features.torso_angle_deg is not None
            and features.torso_angle_deg > self._config.recovery_angle_deg
        ):
            return False
        if state.hip_ref_y is None or state.hip_ref_body_h <= 0.0 or not samples:
            return True
        hip = _hip_midpoint(samples[-1])
        if hip is None:
            return False
        return hip[1] <= state.hip_ref_y + _RECOVERY_HIP_MARGIN * state.hip_ref_body_h

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

    def _standing_reference(self, samples: Sequence[PoseSample]) -> tuple[float, float] | None:
        """(hip_y, body_height) of the highest-standing moment in the
        pre-trigger window. Min hip-y beats "last upright sample": a knee-drop
        sample has an upright torso too and would poison a torso-based pick;
        nothing in the window stands HIGHER than standing hips, and a
        collapse only lowers them."""
        cutoff = samples[-1].ts - self._config.upright_within_s
        best: tuple[float, float] | None = None
        for sample in samples:
            if sample.ts < cutoff:
                continue
            hip = _hip_midpoint(sample)
            if hip is None:
                continue
            height = sample.bbox[3] - sample.bbox[1]
            if height <= 0.0:
                continue
            if best is None or hip[1] < best[0]:
                best = (hip[1], height)
        return best

    def _was_upright_recently(self, samples: Sequence[PoseSample]) -> bool:
        """An upright sample within ``upright_within_s`` before the latest.
        UR Fall ADL lesson (adl-30, 2026-08-27): pose jitter on an
        already-lying body fakes hip velocity (±5 BH/s) with a horizontal
        torso — but that body has been down for seconds. A real fall
        collapses from upright within a fraction of a second (measured
        ≤0.7 s across UR Fall falls; the lying-jitter FPs had been down
        ≥1.4 s). Bbox-height collapse was tried first and rejected: the
        detector box lags the torso, landing inside the real-fall band."""
        cutoff = samples[-1].ts - self._config.upright_within_s
        return any(
            (angle := torso_angle(sample)) is not None
            and angle > self._config.recovery_angle_deg
            and sample.ts >= cutoff
            for sample in samples
        )

    def _velocity_pairs(self, samples: Sequence[PoseSample], pairs: int) -> list[float | None]:
        """BH/s velocity for each of the last ``pairs`` adjacent sample pairs,
        newest first. Adjacent-frame pairs keep the measurement instantaneous
        (a longer baseline would smooth away genuine peaks)."""
        out: list[float | None] = []
        for index in range(len(samples) - 1, len(samples) - pairs - 1, -1):
            if index < 1:
                break
            pair = samples[index - 1 : index + 1]
            height = (pair[0].bbox[3] - pair[0].bbox[1]) + (pair[1].bbox[3] - pair[1].bbox[1])
            if height <= 0.0:
                out.append(None)
                continue
            velocity = compute_fall_features(pair).hip_vertical_velocity
            out.append(velocity / (height / 2.0) if velocity is not None else None)
        return out

    def _sustained_high_bar(self, samples: Sequence[PoseSample]) -> bool:
        """High-bar velocity on ``_HIGH_BAR_PAIRS`` consecutive pairs. UR Fall
        ADL lesson (2026-08-27): jitter spikes hit ±5 BH/s for ONE frame on
        small lying/seated bboxes; a real near-free-fall descent sustains 2.5+
        across consecutive frames (knee-drop falls measured 2 consecutive)."""
        velocities = self._velocity_pairs(samples, _HIGH_BAR_PAIRS)
        if len(velocities) < _HIGH_BAR_PAIRS:
            return False
        return all(
            v is not None and v >= self._config.velocity_high_bar_bh_s
            for v in velocities[:_HIGH_BAR_PAIRS]
        )

    def _triggers(self, features: FallFeatures, samples: Sequence[PoseSample]) -> bool:
        velocity = self._body_heights_per_s(features, samples)
        if velocity is None or not self._was_upright_recently(samples):
            return False
        if self._sustained_high_bar(samples):
            return True
        return (
            velocity >= self._config.velocity_body_heights_per_s
            and features.torso_angle_deg is not None
            and features.torso_angle_deg < self._config.torso_angle_deg
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
