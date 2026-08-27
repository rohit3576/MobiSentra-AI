"""Evidence capture: rolling frame ring + H.264 clip writer (Phase 4, Step 4.4).

``EvidenceBuffer`` keeps the last ``buffer_seconds`` of analyzed frames per
camera as JPEG-compressed, optionally downscaled images (memory-bounded —
raw 720p × 5 s × 10 fps would be ~100 MB; JPEG ~5 MB). When the fall
cascade fires, ``EvidenceWriter`` muxes the window
[trigger − pre_trigger_seconds, fire] into an MP4 (PyAV/libx264 — decoders
agree with cv2, unlike browsers-hostile raw MJPEG) and writes the matching
keypoint samples to a ``.keypoints.json`` sidecar next to the clip, under
``runs/evidence/<camera_id>/``.

Retention hook (runbook 4.4): ``enforce_retention`` caps clips per camera
at ``max_clips_per_camera`` (oldest dropped with their sidecars). Operator
time-based expiry is deliberately NOT here — retention-per-policy lands
with the backend (Phase 8/9); the edge only bounds its own disk.
"""

from __future__ import annotations

import json
import statistics
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import av
import cv2
import numpy as np

from mobisentra.vision.track_history import PoseSample

FALLBACK_FPS: Final = 10


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    buffer_seconds: float = 5.0
    pre_trigger_seconds: float = 2.0
    max_clip_width: int = 960
    jpeg_quality: int = 80
    max_clips_per_camera: int = 200


def fps_from_timestamps(timestamps: list[float]) -> int:
    """Median frame interval → integer fps (av wants rational rates; a clip
    slower than 1 fps plays sped up — evidence only). Fallback without 2+ ts."""
    if len(timestamps) < 2:
        return FALLBACK_FPS
    interval = statistics.median(
        later - earlier for earlier, later in zip(timestamps, timestamps[1:], strict=False)
    )
    if interval <= 0.0:
        return FALLBACK_FPS
    return max(1, round(1.0 / interval))


def _encode_jpeg(frame: np.ndarray, max_width: int, quality: int) -> bytes:
    """Compress a BGR frame for ring storage; downscale + crop to even dims
    (yuv420p requires even dimensions and every ring frame must match)."""
    image = frame
    height, width = image.shape[:2]
    if width > max_width:
        scale = max_width / width
        image = cv2.resize(image, (max_width, max(1, round(height * scale))))
    height, width = image.shape[:2]
    if height % 2 or width % 2:
        image = image[: height - (height % 2), : width - (width % 2)]
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed for evidence frame")
    return encoded.tobytes()


def _decode_jpeg(payload: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)


class EvidenceBuffer:
    """Per-camera rolling window of JPEG frames (one per analyzed frame)."""

    def __init__(self, config: EvidenceConfig | None = None) -> None:
        self._config = config or EvidenceConfig()
        self._ring: deque[tuple[float, bytes]] = deque()

    def __len__(self) -> int:
        return len(self._ring)

    def push(self, ts: float, frame: np.ndarray) -> None:
        self._ring.append(
            (
                ts,
                _encode_jpeg(frame, self._config.max_clip_width, self._config.jpeg_quality),
            )
        )
        horizon = ts - self._config.buffer_seconds
        while self._ring and self._ring[0][0] < horizon:
            self._ring.popleft()

    def snapshot(self, start_ts: float) -> list[tuple[float, np.ndarray]]:
        """Decode all buffered frames from ``start_ts`` onward (may be short
        if the trigger predates the buffer horizon)."""
        return [(ts, _decode_jpeg(payload)) for ts, payload in self._ring if ts >= start_ts]


class EvidenceWriter:
    """Writes MP4 evidence clips + keypoint sidecars under a root folder."""

    def __init__(self, root: Path, config: EvidenceConfig | None = None) -> None:
        self._root = root
        self._config = config or EvidenceConfig()

    def write_fall_clip(
        self,
        *,
        camera_id: str,
        track_id: int,
        trigger_ts: float,
        frames: list[tuple[float, np.ndarray]],
        pose_samples: list[PoseSample],
    ) -> Path:
        """Write the clip (if any frames) + sidecar; returns the clip path,
        or the sidecar path when the frame window was empty."""
        camera_dir = self._root / camera_id
        camera_dir.mkdir(parents=True, exist_ok=True)
        stem = f"fall_track{track_id}_t{int(round(trigger_ts * 1000))}"
        fps = fps_from_timestamps([ts for ts, _ in frames])
        sidecar = camera_dir / f"{stem}.keypoints.json"
        sidecar.write_text(
            json.dumps(
                {
                    "camera_id": camera_id,
                    "track_id": track_id,
                    "trigger_ts": trigger_ts,
                    "fps": fps,
                    "samples": [
                        {
                            "ts": sample.ts,
                            "bbox": list(sample.bbox),
                            "keypoints": [[x, y, c] for x, y, c in sample.keypoints],
                        }
                        for sample in pose_samples
                    ],
                }
            )
        )
        if not frames:
            return sidecar

        clip = camera_dir / f"{stem}.mp4"
        height, width = frames[0][1].shape[:2]
        container = av.open(str(clip), mode="w", options={"movflags": "+faststart"})
        try:
            stream = container.add_stream("libx264", rate=fps)
            stream.width = width
            stream.height = height
            stream.pix_fmt = "yuv420p"
            stream.options = {"crf": "23", "preset": "veryfast"}
            for _, image in frames:
                video_frame = av.VideoFrame.from_ndarray(image, format="bgr24")
                for packet in stream.encode(video_frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        finally:
            container.close()
        return clip

    def enforce_retention(self, camera_id: str) -> list[Path]:
        """Cap clips per camera; return the paths removed (sidecars go too)."""
        camera_dir = self._root / camera_id
        if not camera_dir.is_dir():
            return []
        clips = sorted(
            camera_dir.glob("*.mp4"),
            key=lambda path: path.stat().st_mtime,
        )
        removed: list[Path] = []
        for clip in clips[: max(0, len(clips) - self._config.max_clips_per_camera)]:
            sidecar = clip.with_suffix(".keypoints.json")
            sidecar.unlink(missing_ok=True)
            clip.unlink()
            removed.append(clip)
        return removed
