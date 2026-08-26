"""Per-camera track history ring buffer (Phase 2, plan §5).

Pure logic — no ultralytics, no I/O. Feeds zones (Phase 3), fall (Phase 4)
and fight (Phase 5) analytics with recent per-track motion.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from mobisentra.vision.pose import Keypoint, TrackedPose
from mobisentra.vision.tracker import TrackedPerson


@dataclass(frozen=True)
class TrackSample:
    ts: float
    bbox: tuple[float, float, float, float]
    center: tuple[float, float]
    confidence: float


@dataclass(frozen=True)
class PoseSample:
    ts: float
    keypoints: tuple[Keypoint, ...]


def bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


class TrackHistory:
    """Bounded per-track sample buffer with staleness purge.

    capacity_seconds bounds memory per track; purge(now) drops tracks whose
    last sample is older than stale_seconds (e.g. after ID release).
    """

    def __init__(self, capacity_seconds: float = 10.0, stale_seconds: float = 15.0) -> None:
        self._tracks: dict[int, deque[TrackSample]] = {}
        self._poses: dict[int, deque[PoseSample]] = {}
        self._capacity_seconds = capacity_seconds
        self._stale_seconds = stale_seconds

    @property
    def capacity_seconds(self) -> float:
        return self._capacity_seconds

    def update(self, ts: float, people: list[TrackedPerson]) -> None:
        for person in people:
            samples = self._tracks.setdefault(person.track_id, deque())
            samples.append(
                TrackSample(
                    ts=ts,
                    bbox=person.bbox,
                    center=bbox_center(person.bbox),
                    confidence=person.confidence,
                )
            )
        self._trim(now=ts)

    def update_poses(self, ts: float, poses: list[TrackedPose]) -> None:
        """Store per-track skeletons (Phase 4); separate buffer, same trim."""
        for pose in poses:
            samples = self._poses.setdefault(pose.track_id, deque())
            samples.append(PoseSample(ts=ts, keypoints=pose.keypoints))
        self._trim(now=ts)

    def _trim(self, now: float) -> None:
        horizon = now - self._capacity_seconds
        for samples in self._tracks.values():
            while samples and samples[0].ts <= horizon:
                samples.popleft()
        for samples in self._poses.values():
            while samples and samples[0].ts <= horizon:
                samples.popleft()

    def purge(self, now: float) -> list[int]:
        """Drop tracks not seen for stale_seconds; return their ids."""
        stale_ids = [
            track_id
            for track_id, samples in self._tracks.items()
            if not samples or now - samples[-1].ts > self._stale_seconds
        ]
        stale_ids += [
            track_id
            for track_id, samples in self._poses.items()
            if track_id not in self._tracks
            and (not samples or now - samples[-1].ts > self._stale_seconds)
        ]
        for track_id in stale_ids:
            self._tracks.pop(track_id, None)
            self._poses.pop(track_id, None)
        return stale_ids

    def track_ids(self) -> list[int]:
        return list(self._tracks.keys())

    def history(self, track_id: int, seconds: float | None = None) -> list[TrackSample]:
        samples = self._tracks.get(track_id)
        if samples is None:
            return []
        if seconds is None:
            return list(samples)
        cutoff = samples[-1].ts - seconds
        return [s for s in samples if s.ts >= cutoff]

    def last_sample(self, track_id: int) -> TrackSample | None:
        samples = self._tracks.get(track_id)
        return samples[-1] if samples else None

    def pose_history(self, track_id: int, seconds: float | None = None) -> list[PoseSample]:
        samples = self._poses.get(track_id)
        if samples is None:
            return []
        if seconds is None:
            return list(samples)
        cutoff = samples[-1].ts - seconds
        return [s for s in samples if s.ts >= cutoff]
