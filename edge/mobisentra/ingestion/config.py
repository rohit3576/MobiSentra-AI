"""Camera registry loading + validation (Phase 0, Step 0.10).

The registry is YAML describing every camera the edge pipeline consumes:
sources, zones (normalized polygons), and event thresholds. ``sample://`` URLs
point at bundled sample videos so the whole system runs without hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when the camera registry YAML is invalid."""


class ZoneType(StrEnum):
    """Semantic role of a zone — decides which event logic applies."""

    OCCUPANCY = "occupancy"
    RESTRICTED = "restricted"
    DOOR = "door"


@dataclass(frozen=True)
class ZoneConfig:
    name: str
    zone_type: ZoneType
    polygon: tuple[tuple[float, float], ...]
    max_capacity: int | None = None


@dataclass(frozen=True)
class Thresholds:
    occupancy_confirm_frames: int = 30
    restricted_loiter_seconds: float = 5.0
    door_obstruct_seconds: float = 3.0


@dataclass(frozen=True)
class CameraConfig:
    id: str
    source: str
    vehicle_id: str
    zones: dict[str, ZoneConfig] = field(default_factory=dict)
    thresholds: Thresholds = field(default_factory=Thresholds)
    analyze_every_n_frames: int = 1


def _require_str(cam: dict[str, Any], key: str, cam_id: str) -> str:
    value = cam.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"camera '{cam_id}': '{key}' must be a non-empty string")
    return value


def _parse_polygon(raw: object, camera_id: str, zone_name: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw, list) or len(raw) < 3:
        raise ConfigError(
            f"camera '{camera_id}' zone '{zone_name}': polygon needs >= 3 [x, y] points"
        )
    points: list[tuple[float, float]] = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ConfigError(f"camera '{camera_id}' zone '{zone_name}': each point must be [x, y]")
        coords: list[float] = []
        for value in point:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ConfigError(
                    f"camera '{camera_id}' zone '{zone_name}': coordinates must be numbers"
                )
            if not 0.0 <= float(value) <= 1.0:
                raise ConfigError(
                    f"camera '{camera_id}' zone '{zone_name}': coordinates must be "
                    f"normalized to [0.0, 1.0], got {value}"
                )
            coords.append(float(value))
        points.append((coords[0], coords[1]))
    return tuple(points)


def _parse_zone(name: str, spec: dict[str, Any], camera_id: str) -> ZoneConfig:
    raw_type = spec.get("type")
    if not isinstance(raw_type, str):
        raise ConfigError(
            f"camera '{camera_id}' zone '{name}': 'type' is required "
            f"(one of: {', '.join(t.value for t in ZoneType)})"
        )
    try:
        zone_type = ZoneType(raw_type)
    except ValueError:
        raise ConfigError(
            f"camera '{camera_id}' zone '{name}': unknown type '{raw_type}' "
            f"(expected one of: {', '.join(t.value for t in ZoneType)})"
        ) from None

    raw_capacity = spec.get("max_capacity")
    if zone_type is ZoneType.OCCUPANCY:
        if isinstance(raw_capacity, bool) or not isinstance(raw_capacity, int) or raw_capacity < 1:
            raise ConfigError(
                f"camera '{camera_id}' zone '{name}': occupancy zones need "
                f"'max_capacity' as an integer >= 1"
            )
    elif raw_capacity is not None:
        raise ConfigError(
            f"camera '{camera_id}' zone '{name}': 'max_capacity' is only valid "
            f"for occupancy zones (zone type is '{zone_type.value}')"
        )

    return ZoneConfig(
        name=name,
        zone_type=zone_type,
        polygon=_parse_polygon(spec.get("polygon"), camera_id, name),
        max_capacity=raw_capacity if zone_type is ZoneType.OCCUPANCY else None,
    )


def _parse_thresholds(raw: object, camera_id: str) -> Thresholds:
    if raw is None:
        return Thresholds()
    if not isinstance(raw, dict):
        raise ConfigError(f"camera '{camera_id}': 'thresholds' must be a mapping")
    defaults = Thresholds()
    return Thresholds(
        occupancy_confirm_frames=_positive_int(
            raw.get("occupancy_confirm_frames", defaults.occupancy_confirm_frames),
            camera_id,
            "occupancy_confirm_frames",
        ),
        restricted_loiter_seconds=_positive_float(
            raw.get("restricted_loiter_seconds", defaults.restricted_loiter_seconds),
            camera_id,
            "restricted_loiter_seconds",
        ),
        door_obstruct_seconds=_positive_float(
            raw.get("door_obstruct_seconds", defaults.door_obstruct_seconds),
            camera_id,
            "door_obstruct_seconds",
        ),
    )


def _positive_int(value: object, camera_id: str, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"camera '{camera_id}': '{key}' must be an integer >= 1")
    return value


def _positive_float(value: object, camera_id: str, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"camera '{camera_id}': '{key}' must be a number > 0")
    return float(value)


def _parse_camera(raw: object) -> CameraConfig:
    if not isinstance(raw, dict):
        raise ConfigError("each entry in 'cameras' must be a mapping")
    camera_id = raw.get("id")
    if not isinstance(camera_id, str) or not camera_id.strip():
        raise ConfigError("each camera needs a non-empty string 'id'")

    zones_raw = raw.get("zones", {})
    if not isinstance(zones_raw, dict):
        raise ConfigError(f"camera '{camera_id}': 'zones' must be a mapping")
    zones = {
        name: _parse_zone(name, spec, camera_id)
        for name, spec in zones_raw.items()
        if isinstance(spec, dict) and spec.get("polygon")
    }
    for name, spec in zones_raw.items():
        if spec in (None, [], {}):
            continue
        if not isinstance(spec, dict):
            raise ConfigError(f"camera '{camera_id}' zone '{name}': must be a mapping")

    return CameraConfig(
        id=camera_id,
        source=_require_str(raw, "source", camera_id),
        vehicle_id=_require_str(raw, "vehicle_id", camera_id),
        zones=zones,
        thresholds=_parse_thresholds(raw.get("thresholds"), camera_id),
        analyze_every_n_frames=_positive_int(
            raw.get("analyze_every_n_frames", 1), camera_id, "analyze_every_n_frames"
        ),
    )


def load_cameras(path: str | Path) -> list[CameraConfig]:
    """Load and validate a camera registry YAML file."""
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"camera registry not found: {file}")
    root = yaml.safe_load(file.read_text())
    if not isinstance(root, dict) or not isinstance(root.get("cameras"), list):
        raise ConfigError("registry root must be a mapping with a 'cameras' list")
    if not root["cameras"]:
        raise ConfigError("'cameras' list must contain at least one camera")

    cameras = [_parse_camera(entry) for entry in root["cameras"]]
    ids = [camera.id for camera in cameras]
    duplicates = {camera_id for camera_id in ids if ids.count(camera_id) > 1}
    if duplicates:
        raise ConfigError(f"duplicate camera ids: {sorted(duplicates)}")
    return cameras
