"""TrackHistory tests (pure logic, no ultralytics)."""

from __future__ import annotations

from mobisentra.vision.track_history import TrackHistory, bbox_center
from mobisentra.vision.tracker import TrackedPerson


def person(track_id: int, x: float = 100.0) -> TrackedPerson:
    return TrackedPerson(track_id=track_id, bbox=(x, 50.0, x + 20.0, 120.0), confidence=0.9)


def test_update_and_query():
    history = TrackHistory()
    history.update(1.0, [person(7), person(8)])
    history.update(1.5, [person(7, x=110.0)])
    assert sorted(history.track_ids()) == [7, 8]
    assert len(history.history(7)) == 2
    assert len(history.history(8)) == 1
    assert history.last_sample(7).center == bbox_center((110.0, 50.0, 130.0, 120.0))


def test_capacity_trims_old_samples():
    history = TrackHistory(capacity_seconds=10.0)
    for t in range(20):
        history.update(float(t), [person(1)])
    samples = history.history(1)
    assert samples[0].ts >= 10.0
    assert len(samples) == 10


def test_history_window_relative_to_last():
    history = TrackHistory()
    for t in range(10):
        history.update(float(t), [person(1)])
    window = history.history(1, seconds=2.5)
    assert [s.ts for s in window] == [7.0, 8.0, 9.0]


def test_purge_drops_stale_tracks():
    history = TrackHistory(stale_seconds=15.0)
    history.update(0.0, [person(1), person(2)])
    history.update(5.0, [person(2)])
    purged = history.purge(now=20.0)
    assert purged == [1]
    assert history.track_ids() == [2]


def test_purge_keeps_empty_buffer_safe():
    history = TrackHistory()
    assert history.purge(now=100.0) == []
    assert history.track_ids() == []


def test_unknown_track_returns_empty():
    history = TrackHistory()
    history.update(0.0, [person(1)])
    assert history.history(999) == []
    assert history.last_sample(999) is None
