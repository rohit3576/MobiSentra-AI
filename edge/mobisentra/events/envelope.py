"""CloudEvents v0 envelope builder (Phase 6, Step 6.1a).

Pure translation layer: internal candidate rows (``events/sink.EventRow``)
→ schema-v0 ``data`` payloads + CloudEvents 1.0 envelopes
(``schemas/events/v0/``). No I/O, no clock reads — **all time semantics
anchor to the row's stream ``ts``** (occurrence time), never wall-clock, so
benchmarks replaying footage faster than realtime produce correct event
times and cooldown math stays replay-safe. The only nondeterminism is the
envelope ``id``, produced by an injectable ``id_factory`` (uuid4 in
production; fixed sequences in golden tests).

Severity is passed in per call (resolved by the caller); the config-driven
mapper lands in Step 6.2. ``event_type`` defaults to the row kind; the
optional override exists for the 6.2 occupancy rule (into-``over`` band →
the reserved ``overcrowding`` kind).

The enum constants below mirror the shared JSON Schemas; a drift-guard test
asserts they stay identical to the schema files.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Final, Literal

from mobisentra.events.sink import EventRow

SPECVERSION: Final = "1.0"
TYPE_PREFIX: Final = "org.mobisentra.event."
SOURCE_PREFIX: Final = "/mobisentra/"
DATA_CONTENT_TYPE: Final = "application/json"

SEVERITIES: Final[tuple[str, ...]] = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
EVENT_TYPES: Final[tuple[str, ...]] = (
    "fall_detected",
    "altercation_suspected",
    "overcrowding",
    "restricted_zone_entry",
    "door_obstruction",
    "person_down",
    "occupancy_level_change",
)

Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Rule-based kinds carry no model confidence; the schema requires the field,
# so they report 1.0 (deterministic count/dwell rules — fabricating a score
# would be worse). Fall/fight rows pass their real value through.
DEFAULT_CONFIDENCE: Final = 1.0

# Kind-specific diagnostics worth keeping for dashboards (schema allows
# additional properties). Keys already mapped to named fields are excluded.
_DIAGNOSTIC_KEYS: Final = (
    "from_band",
    "to_band",
    "count",
    "ratio",
    "dwell_seconds",
    "first_seen_ts",
    "door_state",
    "trigger_ts",
    "action_score",
)


def iso_timestamp(ts: float) -> str:
    """Epoch seconds → RFC 3339 UTC string (``Z`` suffix, like the schema
    example; jsonschema ``format`` checks stay advisory in Draft-07)."""
    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def _tracks(row: EventRow) -> list[int]:
    if "track_a" in row and "track_b" in row:  # fight pair
        return [row["track_a"], row["track_b"]]
    if "track_id" in row:  # fall / dwell subjects
        return [row["track_id"]]
    return []  # zone-wide kinds (occupancy bands)


def _require(row: EventRow, key: str) -> object:
    if key not in row:
        raise ValueError(f"event row missing required key {key!r}: {row!r}")
    return row[key]


def to_payload(
    row: EventRow,
    *,
    severity: Severity,
    model_versions: Mapping[str, str],
    event_type: str | None = None,
) -> dict[str, object]:
    """Row → schema-v0 ``data`` payload. Fails fast on unknown kinds,
    non-enum severity, missing required keys, or out-of-range confidence
    (no clamping — silent fixes would hide upstream bugs)."""
    kind = str(event_type if event_type is not None else _require(row, "kind"))
    if kind not in EVENT_TYPES:
        raise ValueError(f"unknown event type {kind!r} (not in schemas v0 enum)")
    if severity not in SEVERITIES:
        raise ValueError(f"severity {severity!r} not in {SEVERITIES}")
    confidence = float(row.get("confidence", DEFAULT_CONFIDENCE))
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence {confidence} outside [0, 1]")
    payload: dict[str, object] = {
        "event_type": kind,
        "severity": severity,
        "camera_id": str(_require(row, "camera_id")),
        "location": row.get("zone"),
        "tracks": _tracks(row),
        "confidence": confidence,
        "timestamp": iso_timestamp(float(_require(row, "ts"))),
        "evidence_ref": row.get("evidence_ref"),
        "model_versions": dict(model_versions),
    }
    for key in _DIAGNOSTIC_KEYS:
        if key in row:
            payload[key] = row[key]
    return payload


class EnvelopeBuilder:
    """Per-camera CloudEvents wrapper: payload + envelope in one call.

    One instance per camera (Step 6.3a composes it from the camera
    registry: ``source = /mobisentra/edge/<vehicle_id>/<camera_id>``).
    Envelope ``time`` is the *occurrence* time (row ``ts``), not emission
    time — CloudEvents semantics, and replay-safe.
    """

    def __init__(
        self,
        *,
        source: str,
        model_versions: Mapping[str, str],
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not source.startswith(SOURCE_PREFIX):
            raise ValueError(f"source {source!r} must start with {SOURCE_PREFIX!r}")
        self._source = source
        self._model_versions = dict(model_versions)
        self._id_factory: Callable[[], str] = (
            id_factory if id_factory is not None else lambda: str(uuid.uuid4())
        )

    @property
    def source(self) -> str:
        return self._source

    def build(
        self, row: EventRow, *, severity: Severity, event_type: str | None = None
    ) -> dict[str, object]:
        """Row → full CloudEvents 1.0 envelope (schema-v0 valid)."""
        data = to_payload(
            row,
            severity=severity,
            model_versions=self._model_versions,
            event_type=event_type,
        )
        event_type_resolved = str(data["event_type"])
        return {
            "specversion": SPECVERSION,
            "id": self._id_factory(),
            "source": self._source,
            "type": TYPE_PREFIX + event_type_resolved,
            "time": data["timestamp"],
            "datacontenttype": DATA_CONTENT_TYPE,
            "data": data,
        }
