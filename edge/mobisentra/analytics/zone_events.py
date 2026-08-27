"""Dwell-time zone events (Phase 3, Steps 3.3–3.4).

Restricted-zone loitering and door obstruction share one mechanic: per
(track, zone) accumulation of wall-clock in-zone time. Kind strings match
schemas v0 (``restricted_zone_entry``, ``door_obstruction``) so Phase 6 can
envelope these candidates without a rename layer.

Semantics (plan §3, Phase 2 fragmentation lesson):

- sightings ≤ ``DROPOUT_TOLERANCE_SECONDS`` apart count as continuous
  in-zone time — detection dropouts must not reset a legitimate loiterer;
- a longer gap starts a fresh accumulation episode (``first_seen_ts`` moves);
- crossing the threshold fires exactly once — the event re-arms only after
  the track is absent ≥ 2× threshold (or its state is purged).

Door events carry ``door_state="unknown"`` — a reserved slot for real door
telemetry over MQTT; no input path exists in Phase 3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, assert_never

from mobisentra.ingestion.config import CameraConfig, ZoneType

DROPOUT_TOLERANCE_SECONDS: Final = 0.5


class DwellEventKind(StrEnum):
    """Candidate event kinds — values are schemas v0 ``event_type`` strings."""

    RESTRICTED_ZONE_ENTRY = "restricted_zone_entry"
    DOOR_OBSTRUCTION = "door_obstruction"


@dataclass(frozen=True, slots=True)
class DwellEvent:
    kind: DwellEventKind
    camera_id: str
    zone: str
    track_id: int
    dwell_seconds: float
    first_seen_ts: float
    ts: float
    door_state: str | None = None


@dataclass(frozen=True, slots=True)
class _Watch:
    kind: DwellEventKind
    threshold_seconds: float
    door_state: str | None


@dataclass(slots=True)
class _TrackDwell:
    """Mutable accumulator — exists to carry dwell state across frames."""

    accumulated: float
    first_seen_ts: float
    last_seen_ts: float
    fired: bool


class DwellTracker:
    """Per-camera dwell accumulator over restricted and door zones."""

    def __init__(self, camera: CameraConfig) -> None:
        self._camera_id = camera.id
        self._watches: dict[str, _Watch] = {}
        for zone in camera.zones.values():
            match zone.zone_type:
                case ZoneType.RESTRICTED:
                    self._watches[zone.name] = _Watch(
                        DwellEventKind.RESTRICTED_ZONE_ENTRY,
                        camera.thresholds.restricted_loiter_seconds,
                        None,
                    )
                case ZoneType.DOOR:
                    self._watches[zone.name] = _Watch(
                        DwellEventKind.DOOR_OBSTRUCTION,
                        camera.thresholds.door_obstruct_seconds,
                        "unknown",
                    )
                case ZoneType.OCCUPANCY:
                    pass  # OccupancyMonitor's domain (Step 3.2)
                case ZoneType.REST:
                    pass  # fall-cascade suppression context (Phase 4.6), not dwell
                case unreachable:
                    assert_never(unreachable)
        self._dwell: dict[tuple[str, int], _TrackDwell] = {}

    def update(self, ts: float, membership: Mapping[str, set[int]]) -> list[DwellEvent]:
        """Feed one analyzed frame's zone membership; returns events fired now."""
        events: list[DwellEvent] = []
        for zone_name, watch in self._watches.items():
            for track_id in membership.get(zone_name, frozenset()):
                event = self._accumulate(zone_name, watch, track_id, ts)
                if event is not None:
                    events.append(event)
        self._purge(ts)
        events.sort(key=lambda event: (event.zone, event.track_id))
        return events

    def _accumulate(
        self, zone_name: str, watch: _Watch, track_id: int, ts: float
    ) -> DwellEvent | None:
        state = self._dwell.get((zone_name, track_id))
        if state is None:
            state = _TrackDwell(accumulated=0.0, first_seen_ts=ts, last_seen_ts=ts, fired=False)
            self._dwell[(zone_name, track_id)] = state
        else:
            gap = ts - state.last_seen_ts
            if gap <= DROPOUT_TOLERANCE_SECONDS:
                state.accumulated += gap
            else:
                state.accumulated = 0.0
                state.first_seen_ts = ts
            state.last_seen_ts = ts

        if state.accumulated < watch.threshold_seconds or state.fired:
            return None
        state.fired = True
        return DwellEvent(
            kind=watch.kind,
            camera_id=self._camera_id,
            zone=zone_name,
            track_id=track_id,
            dwell_seconds=state.accumulated,
            first_seen_ts=state.first_seen_ts,
            ts=ts,
            door_state=watch.door_state,
        )

    def _purge(self, ts: float) -> None:
        """Drop states whose track has been absent ≥ 2× threshold (re-arm)."""
        stale = [
            key
            for key, state in self._dwell.items()
            if ts - state.last_seen_ts >= 2.0 * self._watches[key[0]].threshold_seconds
        ]
        for key in stale:
            del self._dwell[key]
