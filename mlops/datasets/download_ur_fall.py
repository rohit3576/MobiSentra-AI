#!/usr/bin/env python3
"""Download the UR Fall Detection dataset (MP4 convenience encodings).

Phase 4 Step 4.5. Fetches the per-sequence MP4s the dataset page links
(cam0 = horizontal Kinect view — the standard URFD benchmark camera) into
``ur_fall/`` and writes ``ur_fall/manifest.csv``. Resumable: existing files
with a non-zero size and an ``ftyp`` MP4 signature are skipped.

License: CC BY-NC-SA 4.0, benchmark use only — see SOURCES.md next to this
script. Clips are gitignored and never committed.

Usage (from repo root, any Python ≥3.10; stdlib only):
    python mlops/datasets/download_ur_fall.py [--out mlops/datasets/ur_fall]
        [--include-cam1]   # also fetch ceiling-view falls (top view; not
                           # part of the gate protocol — pose is degenerate)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _ssl_context() -> ssl.SSLContext:
    """Verified context; prefer certifi's CA bundle (macOS system Python
    often has no default trust store for urllib)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

BASE = "https://fenix.ur.edu.pl/~mkepski/ds/data"
FALLS = 30
ADLS = 40
SLEEP_S = 0.25  # polite spacing against an academic host
MANIFEST_FIELDS = ["file", "split", "camera", "label", "bytes", "sha256", "url"]


def sequence_urls(include_cam1: bool) -> list[tuple[str, str, str]]:
    """(filename, split, label) triples in benchmark order."""
    rows: list[tuple[str, str, str]] = []
    for i in range(1, FALLS + 1):
        rows.append((f"fall-{i:02d}-cam0.mp4", "falls", "fall"))
    if include_cam1:
        for i in range(1, FALLS + 1):
            rows.append((f"fall-{i:02d}-cam1.mp4", "falls", "fall_topview"))
    for i in range(1, ADLS + 1):
        rows.append((f"adl-{i:02d}-cam0.mp4", "adl", "adl"))
    return rows


def looks_like_mp4(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return len(head) == 12 and b"ftyp" in head and path.stat().st_size > 10_000


def fetch(url: str, dest: Path) -> tuple[int, str]:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60, context=_ssl_context()) as resp, tmp.open("wb") as fh:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
    data_hash = hashlib.sha256()
    size = 0
    with tmp.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 16)
            if not chunk:
                break
            data_hash.update(chunk)
            size += len(chunk)
    tmp.replace(dest)
    return size, data_hash.hexdigest()


def fetch_one(out: Path, filename: str, split: str, label: str) -> dict[str, str] | None:
    """Fetch one sequence; returns manifest row, None on failure (printed)."""
    dest = out / filename
    url = f"{BASE}/{filename}"
    if dest.exists() and looks_like_mp4(dest):
        print(f"[skip] {filename} (already present)")
        size, digest = dest.stat().st_size, "-"
    else:
        try:
            size, digest = fetch(url, dest)
        except Exception as exc:  # noqa: BLE001 — report and continue with the rest
            print(f"[FAIL] {filename}: {exc}", file=sys.stderr)
            return None
        print(f"[ok] {filename} {size / 1e6:.2f} MB")
        time.sleep(SLEEP_S)
    return {
        "file": filename,
        "split": split,
        "camera": "cam1" if "cam1" in filename else "cam0",
        "label": label,
        "bytes": str(size),
        "sha256": digest,
        "url": url,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "ur_fall")
    parser.add_argument("--include-cam1", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="parallel downloads (host throttles per connection; 2026-08-27: "
        "~90 s/file serial vs ~15 s/file at 6 workers)",
    )
    args = parser.parse_args(argv)

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    sequences = sequence_urls(args.include_cam1)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_one, out, *sequence) for sequence in sequences]
        manifest_rows = [row for row in (future.result() for future in futures) if row]
    done_files = {r["file"] for r in manifest_rows}
    failures = [s[0] for s in sequences if s[0] not in done_files]

    manifest = out / "manifest.csv"
    with manifest.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(manifest_rows)
    print(f"[done] {len(manifest_rows)} files indexed -> {manifest}")
    if failures:
        print(f"[warn] {len(failures)} failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
