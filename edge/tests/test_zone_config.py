"""Typed ZoneConfig parsing tests (Phase 3, Step 3.1).

Zone types: occupancy (requires max_capacity), restricted, door (capacity
rejected). Explicit ``type`` survives zone renames — no name-based typing.
"""

from pathlib import Path

import pytest

from mobisentra.ingestion.config import ConfigError, ZoneType, load_cameras

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def _registry(zone_yaml: str) -> str:
    return (
        "cameras:\n"
        "  - id: CAM_A\n"
        "    source: sample://videos/a.mp4\n"
        "    vehicle_id: V1\n"
        f"    zones:\n{zone_yaml}"
    )


TRIANGLE = "[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]"


def test_parses_rest_zone_without_capacity(tmp_path):
    file = tmp_path / "cameras.yaml"
    file.write_text(_registry(f"      bed:\n        type: rest\n        polygon: {TRIANGLE}\n"))

    zone = load_cameras(file)[0].zones["bed"]

    assert zone.zone_type is ZoneType.REST
    assert zone.max_capacity is None


def test_parses_all_three_zone_types(tmp_path):
    registry = _registry(
        "      occ:\n"
        "        type: occupancy\n"
        f"        polygon: {TRIANGLE}\n"
        "        max_capacity: 40\n"
        "      no_go:\n"
        "        type: restricted\n"
        f"        polygon: {TRIANGLE}\n"
        "      dr:\n"
        "        type: door\n"
        f"        polygon: {TRIANGLE}\n"
    )
    file = tmp_path / "cameras.yaml"
    file.write_text(registry)

    zones = load_cameras(file)[0].zones

    assert zones["occ"].zone_type is ZoneType.OCCUPANCY
    assert zones["occ"].max_capacity == 40
    assert zones["no_go"].zone_type is ZoneType.RESTRICTED
    assert zones["no_go"].max_capacity is None
    assert zones["dr"].zone_type is ZoneType.DOOR
    assert zones["dr"].max_capacity is None


@pytest.mark.parametrize(
    "zone_yaml",
    [
        # type missing entirely
        f"      z:\n        polygon: {TRIANGLE}\n",
        # unknown type string
        f"      z:\n        type: staircase\n        polygon: {TRIANGLE}\n",
        # occupancy zone without max_capacity
        f"      z:\n        type: occupancy\n        polygon: {TRIANGLE}\n",
        # occupancy zone with zero max_capacity
        f"      z:\n        type: occupancy\n        polygon: {TRIANGLE}\n"
        "        max_capacity: 0\n",
        # restricted zone carrying max_capacity
        f"      z:\n        type: restricted\n        polygon: {TRIANGLE}\n"
        "        max_capacity: 10\n",
        # door zone carrying max_capacity
        f"      z:\n        type: door\n        polygon: {TRIANGLE}\n        max_capacity: 10\n",
    ],
    ids=[
        "missing-type",
        "unknown-type",
        "occupancy-without-capacity",
        "occupancy-zero-capacity",
        "restricted-with-capacity",
        "door-with-capacity",
    ],
)
def test_invalid_zone_specs_raise(tmp_path, zone_yaml):
    file = tmp_path / "cameras.yaml"
    file.write_text(_registry(zone_yaml))
    with pytest.raises(ConfigError):
        load_cameras(file)


def test_bundled_registries_carry_typed_zones():
    for name in ("cameras.yaml", "sample-cameras.yaml"):
        cameras = load_cameras(CONFIGS / name)
        for camera in cameras:
            for zone in camera.zones.values():
                assert zone.zone_type in set(ZoneType), f"{name}:{camera.id}:{zone.name}"
                if zone.zone_type is ZoneType.OCCUPANCY:
                    assert zone.max_capacity is not None
