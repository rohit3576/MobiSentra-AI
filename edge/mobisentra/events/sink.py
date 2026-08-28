"""JSONL event sink (Phase 3, Day 4).

One file per camera at ``runs/events/<camera_id>.jsonl`` (gitignored).
Rows are internal candidate events — Phase 6 wraps them into CloudEvents
envelopes with severity; Phase 7 ships them over MQTT. Each line is
flushed so a live ``tail -f`` follows events during a run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO, TypedDict


class EventRow(TypedDict, total=False):
    """Shape of a candidate event row (keys per event kind)."""

    kind: str
    camera_id: str
    zone: str
    track_id: int
    track_a: int
    track_b: int
    ts: float
    first_seen_ts: float
    dwell_seconds: float
    door_state: str | None
    from_band: str
    to_band: str
    count: int
    ratio: float
    trigger_ts: float
    confidence: float
    action_score: float
    evidence_ref: str


class JsonlEventWriter:
    """Appends event rows as JSON lines to one file. Mutable file resource."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = path.open("w")

    def write(self, row: EventRow) -> None:
        self._file.write(json.dumps(row) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
