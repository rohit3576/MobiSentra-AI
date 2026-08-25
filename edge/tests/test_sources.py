"""Source resolver tests (Phase 1, plan §6.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mobisentra.ingestion.config import ConfigError
from mobisentra.ingestion.sources import SAMPLE_DIR, resolve_source


def make_video(directory: Path, name: str) -> Path:
    import cv2
    import numpy as np

    path = directory / name
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (64, 48))
    writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()
    return path


def test_sample_source_resolves_against_directory(tmp_path):
    videos = tmp_path / "videos"
    videos.mkdir()
    clip = make_video(videos, "clip.mp4")

    spec = resolve_source("sample://videos/clip.mp4", sample_dir=videos)
    assert spec.kind == "sample"
    assert spec.paced is True
    assert spec.path == clip
    assert spec.capture_arg == str(clip)


def test_sample_source_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="sample video not found"):
        resolve_source("sample://videos/ghost.mp4", sample_dir=tmp_path / "videos")


def test_file_source_resolves(tmp_path):
    clip = make_video(tmp_path, "anywhere.mp4")
    spec = resolve_source(f"file://{clip}")
    assert spec.kind == "file"
    assert spec.paced is True
    assert spec.path == clip


def test_file_source_missing_raises(tmp_path):
    with pytest.raises(ConfigError, match="video file not found"):
        resolve_source(f"file://{tmp_path / 'nope.mp4'}")


def test_webcam_index():
    spec = resolve_source("0")
    assert spec.kind == "webcam"
    assert spec.capture_arg == 0
    assert spec.paced is False


@pytest.mark.parametrize("url", ["rtsp://cam.local/stream", "rtsps://cam.local/s"])
def test_rtsp_sources(url):
    spec = resolve_source(url)
    assert spec.kind == "rtsp"
    assert spec.capture_arg == url
    assert spec.paced is False


@pytest.mark.parametrize("bad", ["", "http://x/y.mp4", "/abs/nope.mp4"])
def test_unsupported_sources_raise(bad):
    with pytest.raises(ConfigError, match="unsupported source"):
        resolve_source(bad)


def test_empty_sample_scheme_raises(tmp_path):
    with pytest.raises(ConfigError):
        resolve_source("sample://", sample_dir=tmp_path / "videos")


def test_bundled_sample_dir_exists():
    assert SAMPLE_DIR.is_dir()
