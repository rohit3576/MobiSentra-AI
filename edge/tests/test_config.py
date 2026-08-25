"""Camera registry loader tests (Phase 0, Step 0.10)."""

from pathlib import Path

import pytest

from mobisentra.ingestion.config import ConfigError, load_cameras

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
REPO_ROOT = Path(__file__).resolve().parents[2]

VALID = """
cameras:
  - id: CAM_A
    source: sample://videos/a.mp4
    vehicle_id: V1
    zones:
      bus_area:
        polygon: [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]
    thresholds:
      occupancy_confirm_frames: 10
  - id: CAM_B
    source: rtsp://u:p@h/stream
    vehicle_id: V2
"""


def _write(tmp_path: Path, content: str) -> Path:
    file = tmp_path / "cameras.yaml"
    file.write_text(content)
    return file


def test_parses_valid_registry(tmp_path):
    cameras = load_cameras(_write(tmp_path, VALID))
    assert [cam.id for cam in cameras] == ["CAM_A", "CAM_B"]
    cam_a = cameras[0]
    assert cam_a.source == "sample://videos/a.mp4"
    assert cam_a.vehicle_id == "V1"
    assert cam_a.zones["bus_area"].polygon == ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9))
    assert cam_a.thresholds.occupancy_confirm_frames == 10
    # defaults applied where not overridden
    assert cam_a.thresholds.restricted_loiter_seconds == 5.0
    assert cameras[1].thresholds.occupancy_confirm_frames == 30
    assert cameras[1].zones == {}


@pytest.mark.parametrize(
    "yaml_text",
    [
        "not-a-mapping",  # root not a mapping
        "cameras: []",  # empty list
        "cameras: [{id: CAM_A}]",  # missing source/vehicle_id
        "cameras: [{id: CAM_A, source: s, vehicle_id: V1},"
        " {id: CAM_A, source: s2, vehicle_id: V2}]",
        # polygon with 2 points
        "cameras: [{id: CAM_A, source: s, vehicle_id: V1,"
        " zones: {z: {polygon: [[0, 0], [1, 1]]}}}]",
        # coordinate out of normalized range
        "cameras: [{id: CAM_A, source: s, vehicle_id: V1,"
        " zones: {z: {polygon: [[0, 0], [1.5, 0], [1, 1]]}}}]",
        # bad threshold
        "cameras: [{id: CAM_A, source: s, vehicle_id: V1,"
        " thresholds: {occupancy_confirm_frames: 0}}]",
    ],
    ids=[
        "root",
        "empty",
        "missing-fields",
        "dup-id",
        "short-polygon",
        "coord-range",
        "bad-threshold",
    ],
)
def test_invalid_registries_raise(tmp_path, yaml_text):
    with pytest.raises(ConfigError):
        load_cameras(_write(tmp_path, yaml_text))


def test_missing_file_raises():
    with pytest.raises(ConfigError, match="not found"):
        load_cameras(REPO_ROOT / "does-not-exist.yaml")


def test_bundled_configs_parse():
    for name in ("cameras.yaml", "sample-cameras.yaml"):
        cameras = load_cameras(CONFIGS / name)
        assert len(cameras) >= 1
        assert all(cam.source for cam in cameras)
