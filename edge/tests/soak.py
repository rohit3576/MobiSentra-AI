"""Soak runner: gate evidence for Phase 1 (runbook Gate 1).

Runs the ingestion pipeline for a fixed duration and writes per-minute
JSONL metrics; the printed summary is transcribed into
Doc/Implementation/phase-1-completion.md.

Usage:
  uv run python tests/soak.py --minutes 60 --config configs/cameras.yaml \
      [--rtsp rtsp://localhost:8554/buscam]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mobisentra.main import run as run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/cameras.yaml"))
    parser.add_argument("--rtsp", action="append", default=[])
    parser.add_argument("--metrics", type=Path, default=None)
    args = parser.parse_args()

    run_pipeline(
        [
            "--config", str(args.config),
            "--minutes", str(args.minutes),
            *sum((["--rtsp", url] for url in args.rtsp), []),
            *(["--metrics", str(args.metrics)] if args.metrics else []),
        ]
    )


if __name__ == "__main__":
    main()
