"""DwellTracker tests (Phase 3, Steps 3.3–3.4).

Synthetic membership timelines — no video. Wall-clock semantics per plan §3:
sightings ≤ 0.5 s apart accumulate continuously (detection dropouts tolerated);
a longer gap resets the accumulation episode; a fired event re-arms only
after the track is absent ≥ 2× threshold. Kind strings match schemas v0.
"""

from mobisentra.analytics.zone_events import DwellEventKind, DwellTracker
from mobisentra.ingestion.config import (
    CameraConfig,
    Thresholds,
    ZoneConfig,
    ZoneType,
)

TRIANGLE = ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9))


def make_camera(restricted_seconds: float = 2.0, door_seconds: float = 1.0) -> CameraConfig:
    return CameraConfig(
        id="CAM_A",
        source="sample://videos/a.mp4",
        vehicle_id="V1",
        zones={
            "no_go": ZoneConfig(name="no_go", zone_type=ZoneType.RESTRICTED, polygon=TRIANGLE),
            "dr": ZoneConfig(name="dr", zone_type=ZoneType.DOOR, polygon=TRIANGLE),
            "occ": ZoneConfig(
                name="occ",
                zone_type=ZoneType.OCCUPANCY,
                polygon=TRIANGLE,
                max_capacity=10,
            ),
        },
        thresholds=Thresholds(
            restricted_loiter_seconds=restricted_seconds,
            door_obstruct_seconds=door_seconds,
        ),
    )


def timeline(tracker: DwellTracker, samples: list[tuple[float, dict[str, set[int]]]]):
    events = []
    for ts, membership in samples:
        events.extend(tracker.update(ts, membership))
    return events


def every(start: float, stop: float, step: float = 0.25) -> list[float]:
    steps = round((stop - start) / step)
    return [round(start + i * step, 10) for i in range(steps + 1)]


def loitering(times: list[float], zone: str = "no_go", track: int = 1):
    return [(t, {zone: {track}}) for t in times]


def test_dwell_accumulates_and_fires_once_at_threshold():
    tracker = DwellTracker(make_camera())
    events = timeline(tracker, loitering(every(0.0, 3.0)))
    assert len(events) == 1
    event = events[0]
    assert event.kind is DwellEventKind.RESTRICTED_ZONE_ENTRY
    assert event.camera_id == "CAM_A"
    assert event.zone == "no_go"
    assert event.track_id == 1
    assert event.dwell_seconds == 2.0
    assert event.first_seen_ts == 0.0
    assert event.ts == 2.0
    assert event.door_state is None


def test_dropout_within_half_second_is_tolerated():
    tracker = DwellTracker(make_camera())
    times = [t for t in every(0.0, 2.5) if t != 0.4]  # one frame missing mid-loiter
    events = timeline(tracker, loitering(times))
    assert len(events) == 1
    assert events[0].ts == 2.0
    assert events[0].first_seen_ts == 0.0


def test_gap_over_half_second_resets_accumulation():
    tracker = DwellTracker(make_camera())
    samples = loitering(every(0.0, 1.0)) + loitering(every(2.0, 4.0))
    events = timeline(tracker, samples)
    assert len(events) == 1
    assert events[0].ts == 4.0
    assert events[0].first_seen_ts == 2.0


def test_fired_event_rearms_only_after_double_threshold_absence():
    tracker = DwellTracker(make_camera())  # threshold 2.0 → re-arm needs ≥ 4.0s absent
    samples = loitering(every(0.0, 3.0))  # fires at 2.0; loiters until 3.0
    samples += [(t, {"no_go": set()}) for t in every(3.25, 5.75)]  # absent 2.75s
    samples += loitering(every(6.0, 8.25))  # returns; re-accumulates past threshold
    events = timeline(tracker, samples)
    assert len(events) == 1  # cooldown blocks the second crossing

    extra = [(t, {"no_go": set()}) for t in every(8.5, 12.5)]  # absent ≥ 4.0s → purged
    extra += loitering(every(12.75, 14.75))  # genuinely new visit
    events += timeline(tracker, extra)
    assert len(events) == 2
    assert events[1].ts == 14.75
    assert events[1].first_seen_ts == 12.75


def test_door_zone_fires_with_door_threshold_and_reserved_state():
    tracker = DwellTracker(make_camera())  # door threshold 1.0
    events = timeline(tracker, loitering(every(0.0, 1.5), zone="dr"))
    assert len(events) == 1
    event = events[0]
    assert event.kind is DwellEventKind.DOOR_OBSTRUCTION
    assert event.zone == "dr"
    assert event.ts == 1.0
    assert event.door_state == "unknown"


def test_occupancy_zones_are_ignored():
    tracker = DwellTracker(make_camera())
    events = timeline(tracker, loitering(every(0.0, 10.0), zone="occ"))
    assert events == []


def test_tracks_accumulate_independently():
    tracker = DwellTracker(make_camera())
    samples = [(t, {"no_go": {1}} if t < 1.0 else {"no_go": {1, 2}}) for t in every(0.0, 3.0)]
    events = timeline(tracker, samples)
    assert [(e.track_id, e.ts) for e in events] == [(1, 2.0), (2, 3.0)]


def test_same_frame_crossings_emit_in_deterministic_order():
    tracker = DwellTracker(make_camera())
    samples = [(t, {"no_go": {5, 2}}) for t in every(0.5, 2.5)]
    events = timeline(tracker, samples)
    assert [e.track_id for e in events] == [2, 5]
    assert all(e.ts == 2.5 for e in events)


def test_zone_specific_thresholds_apply():
    tracker = DwellTracker(make_camera(restricted_seconds=0.5))
    events = timeline(tracker, loitering(every(0.0, 1.0)))
    assert len(events) == 1
    assert events[0].ts == 0.5
