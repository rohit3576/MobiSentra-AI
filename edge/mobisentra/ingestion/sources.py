"""Source string resolution (camera registry `source:` → capture argument).

Supported schemes:
  sample://videos/<name>.mp4   bundled sample video (default; paced loop)
  file:///abs/path.mp4         local video file (paced loop)
  0                            webcam index (live)
  rtsp://… / rtsps://…         network camera (live; TCP transport forced)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mobisentra.ingestion.config import ConfigError

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_data" / "videos"

DEFAULT_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class SourceSpec:
    kind: str
    capture_arg: str | int
    paced: bool
    path: Path | None = None


def resolve_source(source: str, *, sample_dir: Path | None = None) -> SourceSpec:
    s = source.strip()
    if s.startswith("sample://"):
        rel = s.removeprefix("sample://")
        base = (sample_dir or SAMPLE_DIR).parent
        path = base / rel
        if not path.is_file():
            raise ConfigError(
                f"sample video not found: {path} (place clips under edge/sample_data/videos/)"
            )
        return SourceSpec("sample", str(path), True, path)
    if s.startswith("file://"):
        path = Path(s.removeprefix("file://"))
        if not path.is_file():
            raise ConfigError(f"video file not found: {path}")
        return SourceSpec("file", str(path), True, path)
    if s.isdigit():
        return SourceSpec("webcam", int(s), False)
    if s.startswith(("rtsp://", "rtsps://")):
        return SourceSpec("rtsp", s, False)
    raise ConfigError(
        f"unsupported source: {source!r} (expected sample:// | file:// | <webcam index> | rtsp://)"
    )


def open_real_capture(spec: SourceSpec, *, timeout_ms: int = DEFAULT_TIMEOUT_MS):
    """Open a cv2 capture for a resolved spec.

    RTSP streams are forced onto TCP (UDP silently corrupts frames on lossy
    networks) and given open/read timeouts so a dead camera cannot block the
    reader thread forever — the failed read feeds the reconnect path instead.
    """
    import cv2

    if spec.kind == "rtsp":
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    cap = cv2.VideoCapture(spec.capture_arg)
    if spec.kind == "rtsp":
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms)
    return cap
