"""JSONL metrics writer for ingestion runs (soak evidence, live ops)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MinuteStats:
    camera_id: str
    ts: float
    read_fps: float
    consumed_fps: float
    lag_p50_ms: float
    lag_p95_ms: float
    lag_max_ms: float
    dropped: int
    reconnects: int


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(fraction * (len(ordered) - 1)), len(ordered) - 1)
    return ordered[idx]


class MetricsWriter:
    """Append one JSON line per (camera, minute) to a run file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, stats: MinuteStats) -> None:
        with self.path.open("a") as fh:
            fh.write(json.dumps(asdict(stats)) + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]
