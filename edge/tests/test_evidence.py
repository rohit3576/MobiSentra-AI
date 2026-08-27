"""Evidence buffer + clip writer tests (Phase 4, Step 4.4).

``EvidenceBuffer`` keeps a JPEG-compressed 5 s ring per camera; a triggered
fall snapshots the window [trigger − pre, now] and ``EvidenceWriter`` muxes
it into an H.264 MP4 (PyAV) plus a keypoints sidecar. "Playable" is proven
by re-opening the file with cv2 and reading every frame back.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from mobisentra.events.evidence import (
    EvidenceBuffer,
    EvidenceConfig,
    EvidenceWriter,
)
from mobisentra.vision.pose import N_KEYPOINTS
from mobisentra.vision.track_history import PoseSample


def frame(color: int, width: int = 64, height: int = 48) -> np.ndarray:
    return np.full((height, width, 3), color, dtype=np.uint8)


def pose_sample(ts: float) -> PoseSample:
    keypoints = tuple((float(i), float(i), 0.9) for i in range(N_KEYPOINTS))
    return PoseSample(ts=ts, bbox=(1.0, 2.0, 3.0, 4.0), keypoints=keypoints)


def reopen_frame_count(path: Path) -> tuple[int, tuple[int, int]]:
    cap = cv2.VideoCapture(str(path))
    count = 0
    while cap.isOpened():
        ok, _ = cap.read()
        if not ok:
            break
        count += 1
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return count, size


class TestEvidenceBuffer:
    def test_ring_evicts_beyond_buffer_seconds(self):
        buffer = EvidenceBuffer(EvidenceConfig(buffer_seconds=5.0))
        for i in range(12):
            buffer.push(float(i), frame(i))
        assert len(buffer) == 6
        assert [ts for ts, _ in buffer.snapshot(start_ts=0.0)] == [6.0, 7.0, 8.0, 9.0, 10.0, 11.0]

    def test_snapshot_returns_only_frames_from_start_ts(self):
        buffer = EvidenceBuffer()
        for i in range(6):
            buffer.push(float(i), frame(i * 30))
        frames = buffer.snapshot(start_ts=3.5)
        assert [ts for ts, _ in frames] == [4.0, 5.0]

    def test_snapshot_before_any_push_is_empty(self):
        assert EvidenceBuffer().snapshot(start_ts=0.0) == []

    def test_jpeg_roundtrip_preserves_shape_and_content(self):
        buffer = EvidenceBuffer()
        original = frame(200)
        buffer.push(0.0, original)
        [(ts, decoded)] = buffer.snapshot(0.0)
        assert ts == 0.0
        assert decoded.shape == original.shape
        # lossy JPEG: allow a compression delta, not a colour swap
        assert abs(float(decoded.mean()) - float(original.mean())) < 5.0

    def test_wide_frames_downscale_to_max_clip_width_with_even_dims(self):
        buffer = EvidenceBuffer(EvidenceConfig(max_clip_width=320))
        buffer.push(0.0, frame(100, width=641, height=321))
        [(_, decoded)] = buffer.snapshot(0.0)
        assert decoded.shape[1] == 320
        assert decoded.shape[0] % 2 == 0 and decoded.shape[1] % 2 == 0


class TestEvidenceWriter:
    def writer(self, tmp_path: Path, config: EvidenceConfig | None = None) -> EvidenceWriter:
        return EvidenceWriter(tmp_path, config or EvidenceConfig())

    def test_fall_clip_is_playable_mp4_with_all_frames(self, tmp_path: Path):
        writer = self.writer(tmp_path)
        frames = [(float(i), frame(i * 25)) for i in range(8)]
        path = writer.write_fall_clip(
            camera_id="CAM_A",
            track_id=7,
            trigger_ts=12.0,
            frames=frames,
            pose_samples=[pose_sample(11.0 + i * 0.1) for i in range(8)],
        )
        assert path.suffix == ".mp4"
        count, size = reopen_frame_count(path)
        assert count == 8
        assert size == (64, 48)

    def test_sidecar_keypoints_json_written_next_to_clip(self, tmp_path: Path):
        writer = self.writer(tmp_path)
        path = writer.write_fall_clip(
            camera_id="CAM_A",
            track_id=7,
            trigger_ts=1.0,
            frames=[(0.0, frame(10)), (0.1, frame(20))],
            pose_samples=[pose_sample(0.0), pose_sample(0.1)],
        )
        sidecar = path.with_suffix(".keypoints.json")
        assert sidecar.is_file()
        payload = json.loads(sidecar.read_text())
        assert payload["camera_id"] == "CAM_A"
        assert payload["track_id"] == 7
        assert payload["trigger_ts"] == 1.0
        assert [s["ts"] for s in payload["samples"]] == [0.0, 0.1]
        assert len(payload["samples"][0]["keypoints"]) == N_KEYPOINTS

    def test_clip_and_sidecar_land_under_camera_folder(self, tmp_path: Path):
        writer = self.writer(tmp_path)
        path = writer.write_fall_clip(
            camera_id="CAM_X",
            track_id=1,
            trigger_ts=5.0,
            frames=[(4.0, frame(5))],
            pose_samples=[pose_sample(4.0)],
        )
        assert path.parent == tmp_path / "CAM_X"
        assert "track1" in path.name

    def test_empty_frame_window_writes_sidecar_only(self, tmp_path: Path):
        writer = self.writer(tmp_path)
        path = writer.write_fall_clip(
            camera_id="CAM_A",
            track_id=3,
            trigger_ts=9.0,
            frames=[],
            pose_samples=[pose_sample(9.0)],
        )
        assert path.suffix == ".json"
        assert path.is_file()

    def test_retention_drops_oldest_clips_beyond_max(self, tmp_path: Path):
        writer = self.writer(tmp_path, EvidenceConfig(max_clips_per_camera=2))
        for trigger in (1.0, 2.0, 3.0):
            writer.write_fall_clip(
                camera_id="CAM_A",
                track_id=1,
                trigger_ts=trigger,
                frames=[(trigger, frame(10))],
                pose_samples=[pose_sample(trigger)],
            )
        removed = writer.enforce_retention("CAM_A")
        clips = sorted((tmp_path / "CAM_A").glob("*.mp4"))
        assert len(clips) == 2
        assert len(removed) == 1
        # sidecars of removed clips go too
        assert len(list((tmp_path / "CAM_A").glob("*.keypoints.json"))) == 2

    def test_retention_with_no_clips_is_a_noop(self, tmp_path: Path):
        assert self.writer(tmp_path).enforce_retention("CAM_NONE") == []


class TestFpsDerivation:
    @pytest.mark.parametrize(
        ("deltas", "expected"),
        [([0.1] * 5, 10), ([0.033] * 5, 30), ([2.0, 2.0], 1), ([], 10)],
    )
    def test_median_interval_to_fps(self, deltas, expected):
        from mobisentra.events.evidence import fps_from_timestamps

        timestamps = [0.0]
        for delta in deltas:
            timestamps.append(timestamps[-1] + delta)
        assert fps_from_timestamps(timestamps) == expected
