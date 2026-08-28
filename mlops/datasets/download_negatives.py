#!/usr/bin/env python3
"""Download the fight-path negatives corpus (Phase 5, Step 5.4).

Pinned, deterministic fetch of the curated normal-interaction corpus
(hugging, playing, rushing, assisting, crowds) from the Pexels CDN — the
FP-regression input for ``edge/tools/fight_negatives_soak.py`` and Gate
5's 0-alerts criterion. All clips are Pexels License (free use and
redistribution; origin URLs recorded in manifest.csv + SOURCES.md).

Requires ``curl_cffi`` (the Pexels WAF rejects plain HTTP clients):
    pip install curl_cffi
Run from anywhere; writes into ``edge/sample_data/negatives/``:
    python mlops/datasets/download_negatives.py

Resumable: files whose SHA256 matches manifest.csv are skipped. Media is
gitignored (UR Fall posture); SOURCES.md + manifest.csv are committed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import time
from pathlib import Path

from curl_cffi import requests

HEADERS = {"referer": "https://www.pexels.com/", "accept-language": "en-US,en;q=0.9"}
MANIFEST_FIELDS = ["file", "category", "pexels_url", "file_url", "duration_s", "bytes", "sha256"]


def _vid_of(filename: str) -> str:
    return filename.rsplit("_", 1)[-1].removesuffix(".mp4")


def cdn_url(filename: str, rendition: str) -> str:
    vid = _vid_of(filename)
    return f"https://videos.pexels.com/video-files/{vid}/{vid}-{rendition}.mp4"


def pexels_page(filename: str) -> str:
    return f"https://www.pexels.com/video/{_vid_of(filename)}/"


def retry_get(url: str, *, timeout: int, attempts: int = 4):
    last = None
    for i in range(attempts):
        try:
            return requests.get(url, impersonate="safari", headers=HEADERS, timeout=timeout)
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (i + 1))
    raise last


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 16):
            digest.update(chunk)
    return digest.hexdigest()


# (filename, pexels rendition, category, expected_duration_s)
# CDN URL = https://videos.pexels.com/video-files/<id>/<id>-<rendition>.mp4 — pinned 2026-08-28
PINNED: list[tuple[str, str, str, float]] = [
    ("neg_assist_6646612.mp4", "sd_640_360_30fps", "assist", 17.65),
    ("neg_assist_6646617.mp4", "sd_360_640_30fps", "assist", 11.44),
    ("neg_assist_6646654.mp4", "sd_640_360_30fps", "assist", 19.09),
    ("neg_assist_6646663.mp4", "sd_360_640_24fps", "assist", 17.35),
    ("neg_assist_6646681.mp4", "sd_640_360_24fps", "assist", 19.1),
    ("neg_assist_7475255.mp4", "sd_640_360_25fps", "assist", 15.08),
    ("neg_assist_7516772.mp4", "sd_360_640_25fps", "assist", 8.48),
    ("neg_assist_7517374.mp4", "sd_640_360_25fps", "assist", 9.48),
    ("neg_assist_7517378.mp4", "sd_640_360_25fps", "assist", 9.8),
    ("neg_assist_7517381.mp4", "sd_640_360_25fps", "assist", 10.6),
    ("neg_assist_7517383.mp4", "sd_640_360_25fps", "assist", 8.32),
    ("neg_assist_7522204.mp4", "sd_640_360_25fps", "assist", 9.68),
    ("neg_assist_7522211.mp4", "sd_640_360_25fps", "assist", 13.6),
    ("neg_assist_7522214.mp4", "sd_640_360_25fps", "assist", 14.32),
    ("neg_assist_7522219.mp4", "sd_640_360_25fps", "assist", 18.0),
    ("neg_assist_7522220.mp4", "sd_640_360_25fps", "assist", 20.08),
    ("neg_assist_7522221.mp4", "sd_640_360_25fps", "assist", 9.24),
    ("neg_crowd_10177961.mp4", "sd_640_360_30fps", "crowd", 30.8),
    ("neg_crowd_11791314.mp4", "sd_640_360_30fps", "crowd", 59.96),
    ("neg_crowd_13587066.mp4", "sd_640_360_24fps", "crowd", 18.52),
    ("neg_crowd_14393753.mp4", "sd_640_360_30fps", "crowd", 31.13),
    ("neg_crowd_14393758.mp4", "sd_640_360_30fps", "crowd", 21.09),
    ("neg_crowd_15007111.mp4", "sd_640_360_15fps", "crowd", 31.26),
    ("neg_crowd_15007112.mp4", "sd_640_360_15fps", "crowd", 31.52),
    ("neg_crowd_15760904.mp4", "sd_640_360_24fps", "crowd", 18.58),
    ("neg_crowd_16240174.mp4", "sd_360_640_30fps", "crowd", 16.42),
    ("neg_crowd_18456122.mp4", "sd_640_360_30fps", "crowd", 11.48),
    ("neg_crowd_18463428.mp4", "sd_360_640_30fps", "crowd", 8.57),
    ("neg_crowd_18552655.mp4", "sd_640_360_30fps", "crowd", 22.59),
    ("neg_crowd_19085074.mp4", "sd_360_640_30fps", "crowd", 8.0),
    ("neg_crowd_19300221.mp4", "sd_640_360_25fps", "crowd", 8.64),
    ("neg_crowd_19434326.mp4", "sd_360_640_30fps", "crowd", 15.83),
    ("neg_crowd_4346889.mp4", "sd_640_360_30fps", "crowd", 11.37),
    ("neg_crowd_4750042.mp4", "sd_640_360_30fps", "crowd", 13.11),
    ("neg_crowd_4819179.mp4", "sd_640_360_30fps", "crowd", 15.68),
    ("neg_crowd_6574230.mp4", "sd_640_360_25fps", "crowd", 19.68),
    ("neg_crowd_854664.mp4", "sd_640_360_30fps", "crowd", 29.43),
    ("neg_crowd_855586.mp4", "sd_640_360_30fps", "crowd", 15.37),
    ("neg_hug_3704107.mp4", "sd_506_960_25fps", "hug", 9.4),
    ("neg_hug_4588258.mp4", "sd_640_360_25fps", "hug", 13.24),
    ("neg_hug_4668582.mp4", "sd_640_360_24fps", "hug", 10.39),
    ("neg_hug_4800446.mp4", "sd_640_360_24fps", "hug", 10.0),
    ("neg_hug_4800620.mp4", "sd_360_640_24fps", "hug", 12.04),
    ("neg_hug_4801939.mp4", "sd_360_640_24fps", "hug", 8.58),
    ("neg_hug_5045935.mp4", "sd_640_360_30fps", "hug", 8.01),
    ("neg_hug_5087558.mp4", "sd_640_360_25fps", "hug", 9.76),
    ("neg_hug_5272788.mp4", "sd_640_360_30fps", "hug", 10.64),
    ("neg_hug_5359620.mp4", "sd_640_360_30fps", "hug", 19.19),
    ("neg_hug_5518467.mp4", "sd_360_640_30fps", "hug", 20.29),
    ("neg_hug_5616659.mp4", "sd_640_360_25fps", "hug", 18.72),
    ("neg_hug_5616662.mp4", "sd_640_360_25fps", "hug", 14.6),
    ("neg_hug_5616686.mp4", "sd_640_360_25fps", "hug", 8.08),
    ("neg_hug_5737814.mp4", "sd_640_360_25fps", "hug", 12.12),
    ("neg_hug_5737818.mp4", "sd_640_360_25fps", "hug", 21.56),
    ("neg_hug_5745609.mp4", "sd_640_360_25fps", "hug", 14.64),
    ("neg_hug_6104311.mp4", "sd_640_360_25fps", "hug", 20.68),
    ("neg_hug_6149761.mp4", "sd_640_360_30fps", "hug", 13.03),
    ("neg_hug_6149766.mp4", "sd_640_360_30fps", "hug", 8.33),
    ("neg_hug_6149767.mp4", "sd_640_360_30fps", "hug", 21.27),
    ("neg_hug_6149769.mp4", "sd_640_360_30fps", "hug", 10.1),
    ("neg_hug_6149777.mp4", "sd_640_360_30fps", "hug", 10.43),
    ("neg_hug_6149778.mp4", "sd_640_360_30fps", "hug", 13.47),
    ("neg_hug_6149885.mp4", "sd_640_360_30fps", "hug", 11.2),
    ("neg_hug_6150051.mp4", "sd_360_640_30fps", "hug", 8.43),
    ("neg_hug_6307679.mp4", "sd_360_640_24fps", "hug", 10.01),
    ("neg_hug_6570564.mp4", "sd_360_640_25fps", "hug", 25.12),
    ("neg_hug_6570593.mp4", "sd_360_640_25fps", "hug", 32.72),
    ("neg_hug_6967412.mp4", "sd_640_360_25fps", "hug", 12.44),
    ("neg_hug_7059112.mp4", "sd_360_640_24fps", "hug", 10.01),
    ("neg_hug_7119658.mp4", "sd_640_360_25fps", "hug", 11.28),
    ("neg_hug_7251362.mp4", "sd_360_640_25fps", "hug", 9.2),
    ("neg_hug_7351683.mp4", "sd_506_960_30fps", "hug", 29.5),
    ("neg_hug_7983313.mp4", "sd_360_640_24fps", "hug", 10.01),
    ("neg_hug_8503578.mp4", "sd_640_360_24fps", "hug", 13.25),
    ("neg_hug_8644127.mp4", "sd_640_360_24fps", "hug", 16.0),
    ("neg_hug_8878165.mp4", "sd_360_640_25fps", "hug", 9.76),
    ("neg_hug_8899778.mp4", "sd_640_360_25fps", "hug", 14.8),
    ("neg_hug_9750741.mp4", "sd_360_640_24fps", "hug", 40.33),
    ("neg_hug_9787350.mp4", "sd_360_640_25fps", "hug", 14.96),
    ("neg_play_12760966.mp4", "sd_360_640_24fps", "play", 10.01),
    ("neg_play_17731450.mp4", "sd_360_640_30fps", "play", 16.2),
    ("neg_play_19066157.mp4", "sd_640_360_30fps", "play", 17.77),
    ("neg_play_19735391.mp4", "sd_360_640_30fps", "play", 8.78),
    ("neg_play_3677021.mp4", "sd_506_960_25fps", "play", 17.08),
    ("neg_play_3682357.mp4", "sd_506_960_25fps", "play", 16.44),
    ("neg_play_4122468.mp4", "sd_640_360_30fps", "play", 8.91),
    ("neg_play_4691631.mp4", "sd_506_960_25fps", "play", 15.8),
    ("neg_play_4691670.mp4", "sd_506_960_25fps", "play", 43.64),
    ("neg_play_5017797.mp4", "sd_640_360_30fps", "play", 14.95),
    ("neg_play_5272054.mp4", "sd_640_360_30fps", "play", 10.01),
    ("neg_play_5272794.mp4", "sd_640_360_30fps", "play", 11.71),
    ("neg_play_5512139.mp4", "sd_360_640_24fps", "play", 10.01),
    ("neg_play_5601071.mp4", "sd_360_640_24fps", "play", 10.05),
    ("neg_play_5877872.mp4", "sd_640_360_30fps", "play", 8.87),
    ("neg_play_6183268.mp4", "sd_360_640_30fps", "play", 9.43),
    ("neg_play_6299132.mp4", "sd_640_360_25fps", "play", 28.84),
    ("neg_play_6299167.mp4", "sd_640_360_25fps", "play", 16.28),
    ("neg_play_6300728.mp4", "sd_640_360_25fps", "play", 8.28),
    ("neg_play_6651627.mp4", "sd_640_360_25fps", "play", 9.84),
    ("neg_play_6952259.mp4", "sd_640_360_30fps", "play", 10.63),
    ("neg_play_6952294.mp4", "sd_640_360_30fps", "play", 17.77),
    ("neg_play_6952624.mp4", "sd_360_640_30fps", "play", 18.37),
    ("neg_play_7101115.mp4", "sd_640_360_30fps", "play", 13.43),
    ("neg_play_7102473.mp4", "sd_360_640_30fps", "play", 8.61),
    ("neg_play_7330443.mp4", "sd_640_360_25fps", "play", 14.0),
    ("neg_play_7424470.mp4", "sd_640_360_30fps", "play", 20.49),
    ("neg_play_7667875.mp4", "sd_640_360_25fps", "play", 25.76),
    ("neg_play_7667949.mp4", "sd_640_360_25fps", "play", 30.96),
    ("neg_play_7667953.mp4", "sd_640_360_25fps", "play", 16.92),
    ("neg_play_7774573.mp4", "sd_360_640_30fps", "play", 10.33),
    ("neg_play_7844078.mp4", "sd_640_360_25fps", "play", 8.96),
    ("neg_play_8034250.mp4", "sd_360_640_30fps", "play", 8.9),
    ("neg_play_8048056.mp4", "sd_640_360_25fps", "play", 14.36),
    ("neg_play_8100958.mp4", "sd_640_360_25fps", "play", 14.52),
    ("neg_play_8160024.mp4", "sd_640_360_25fps", "play", 14.96),
    ("neg_play_8160571.mp4", "sd_360_640_25fps", "play", 8.28),
    ("neg_play_8206439.mp4", "sd_506_960_25fps", "play", 33.76),
    ("neg_play_8435433.mp4", "sd_640_360_25fps", "play", 19.32),
    ("neg_play_8552684.mp4", "sd_506_960_25fps", "play", 10.96),
    ("neg_play_8751451.mp4", "sd_360_640_30fps", "play", 13.01),
    ("neg_play_8813004.mp4", "sd_640_360_25fps", "play", 14.08),
    ("neg_play_8951290.mp4", "sd_640_360_25fps", "play", 10.84),
    ("neg_play_9067648.mp4", "sd_360_640_25fps", "play", 38.48),
    ("neg_rush_10161869.mp4", "sd_640_360_30fps", "rush", 11.11),
    ("neg_rush_10531835.mp4", "sd_360_640_30fps", "rush", 9.31),
    ("neg_rush_14365420.mp4", "sd_640_360_30fps", "rush", 11.51),
    ("neg_rush_1601247.mp4", "sd_640_360_30fps", "rush", 38.04),
    ("neg_rush_17581212.mp4", "sd_640_360_30fps", "rush", 67.13),
    ("neg_rush_19538635.mp4", "sd_640_360_24fps", "rush", 36.79),
    ("neg_rush_3029447.mp4", "sd_640_360_24fps", "rush", 33.37),
    ("neg_rush_3542101.mp4", "sd_640_360_30fps", "rush", 9.94),
    ("neg_rush_3773325.mp4", "sd_640_360_24fps", "rush", 9.47),
    ("neg_rush_4122942.mp4", "sd_640_360_24fps", "rush", 28.4),
    ("neg_rush_4473914.mp4", "sd_640_360_30fps", "rush", 35.97),
    ("neg_rush_4698492.mp4", "sd_640_360_30fps", "rush", 11.43),
    ("neg_rush_4995943.mp4", "sd_640_360_30fps", "rush", 31.63),
    ("neg_rush_4999693.mp4", "sd_640_360_25fps", "rush", 30.4),
    ("neg_rush_5166397.mp4", "sd_640_360_25fps", "rush", 15.72),
    ("neg_rush_5814570.mp4", "sd_640_360_25fps", "rush", 11.88),
    ("neg_rush_853874.mp4", "sd_640_360_25fps", "rush", 13.96),
    ("neg_rush_853946.mp4", "sd_640_360_25fps", "rush", 14.16),
    ("neg_rush_853949.mp4", "sd_640_360_25fps", "rush", 15.08),
    ("neg_rush_853957.mp4", "sd_640_360_30fps", "rush", 13.9),
    ("neg_rush_853967.mp4", "sd_640_360_25fps", "rush", 12.6),
    ("neg_rush_854100.mp4", "sd_640_360_25fps", "rush", 15.72),
    ("neg_rush_9206569.mp4", "sd_640_360_25fps", "rush", 90.84),
    ("neg_rush_9850435.mp4", "sd_640_360_30fps", "rush", 14.6)
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parents[2] / "edge/sample_data/negatives",
    )
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    known_sha: dict[str, str] = {}
    manifest_path = args.out / "manifest.csv"
    if manifest_path.exists():
        with manifest_path.open() as fh:
            for row in csv.DictReader(fh):
                known_sha[row["file"]] = row["sha256"]

    rows: list[dict[str, str]] = []
    failures: list[str] = []
    for filename, rendition, category, expected_s in PINNED:
        dest = args.out / filename
        url = cdn_url(filename, rendition)
        page = pexels_page(filename)
        if dest.exists() and known_sha.get(filename) == sha256_of(dest):
            rows.append({"file": filename, "category": category, "pexels_url": page,
                         "file_url": url, "duration_s": str(expected_s),
                         "bytes": str(dest.stat().st_size), "sha256": known_sha[filename]})
            print(f"[skip] {filename}")
            continue
        try:
            r = retry_get(url, timeout=120)
            if r.status_code != 200 or len(r.content) < 100_000:
                failures.append(filename)
                print(f"[FAIL] {filename} HTTP {r.status_code}")
                continue
            dest.write_bytes(r.content)
        except Exception as exc:
            failures.append(filename)
            print(f"[FAIL] {filename}: {str(exc)[:70]}")
            continue
        time.sleep(0.5)
        rows.append({"file": filename, "category": category, "pexels_url": page,
                     "file_url": url, "duration_s": str(expected_s),
                     "bytes": str(dest.stat().st_size), "sha256": sha256_of(dest)})
        print(f"[ok] {filename} {expected_s:.0f}s")

    with manifest_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    total_min = sum(float(r["duration_s"]) for r in rows) / 60.0
    print(f"[done] {len(rows)}/{len(PINNED)} clips, {total_min:.1f} min -> {manifest_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
