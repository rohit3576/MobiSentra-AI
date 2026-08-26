"""JSONL event sink tests (Phase 3, Day 4 wiring)."""

import json
from pathlib import Path

from mobisentra.events.sink import JsonlEventWriter


def test_writer_creates_directories_and_roundtrips_rows(tmp_path: Path):
    target = tmp_path / "runs" / "events" / "CAM_A.jsonl"
    writer = JsonlEventWriter(target)

    writer.write({"kind": "door_obstruction", "camera_id": "CAM_A", "ts": 1.5})
    writer.write({"kind": "occupancy_level_change", "camera_id": "CAM_A", "ts": 2.0})
    writer.close()

    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["door_obstruction", "occupancy_level_change"]
    assert rows[0]["ts"] == 1.5


def test_rows_are_readable_while_writer_is_open(tmp_path: Path):
    target = tmp_path / "events.jsonl"
    writer = JsonlEventWriter(target)
    writer.write({"kind": "x", "ts": 0.0})
    assert len(target.read_text().splitlines()) == 1
    writer.close()
