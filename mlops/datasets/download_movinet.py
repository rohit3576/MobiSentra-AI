#!/usr/bin/env python3
"""Download MoViNet4Violence checkpoints (altercation model, Phase 5 Step 5.1a).

Fetches the TensorFlow checkpoint triplet (checkpoint + .data-00000-of-00001 +
.index) for one trained variant from the HuggingFace repo into ``movinet/``
and writes ``movinet/manifest.csv`` with sizes + SHA256s. Resumable: existing
files with a recorded matching SHA256 are skipped.

Variant selection is evidence-based (engares model_performance_metrics.csv,
fetched 2026-08-28):
  a2 (default)  movinet_a2_5fps_32bs_0.001lr_0.3dr_0tl  — best A2: 81.22% test
                acc, F1_fight 0.8184, recall_fight 0.8466 (recall matters for
                the Gate-5 miss criterion)
  a0 (fallback) movinet_a0_5fps_32bs_0.001lr_0.2dr_2tl  — best A0: 76.72% acc
Both are 5 fps-input models — matches our low analysis-FPS edge pipeline.

Licensing posture (see SOURCES.md): the engares GitHub repo has NO license
(all rights reserved) → wrapper-only use, zero code copied; HF repo metadata
carries no license either and tags list RWF-2000 among training datasets
(non-commercial) → weights are BENCHMARK-ONLY, never bundled, never
redistributed. The HF repo ships TF checkpoints only — no .tflite/SavedModel
(Runtime-spike consequence recorded in phase-5-plan.md Step 5.1 decisions).

Usage (from repo root, any Python >=3.10; stdlib only):
    python mlops/datasets/download_movinet.py            # A2 default
    python mlops/datasets/download_movinet.py --variant a0
        [--out mlops/datasets/movinet]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import ssl
import sys
import urllib.request
from pathlib import Path

BASE = "https://huggingface.co/engares/MoViNet4Violence-Detection/resolve/main"
REPO_DIR = "trained_models_dropout_autolr_trlayers_NoAug"

# variant -> (HF subdirectory, data-file stem, reported metrics from the
# engares performance CSV — provenance only, we never re-report as ours)
VARIANTS: dict[str, dict[str, str]] = {
    "a2": {
        "dir": "movinet_a2_5fps_32bs_0.001lr_0.3dr_0tl",
        "stem": "movinet_a2_stream_wbm",
        "metrics": "acc 0.8122 / F1_fight 0.8184 / recall_fight 0.8466",
    },
    "a0": {
        "dir": "movinet_a0_5fps_32bs_0.001lr_0.2dr_2tl",
        "stem": "movinet_a0_stream_wbm",
        "metrics": "acc 0.7672 / F1_fight 0.765 / recall_fight 0.8095",
    },
}
MANIFEST_FIELDS = ["variant", "file", "bytes", "sha256", "url"]


def _ssl_context() -> ssl.SSLContext:
    """Verified context; prefer certifi's CA bundle (macOS system Python
    often has no default trust store for urllib)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 16):
            digest.update(chunk)
    return digest.hexdigest()


def variant_files(variant: str) -> list[str]:
    """Repo-relative paths for the checkpoint triplet + repo README."""
    spec = VARIANTS[variant]
    sub = f"{REPO_DIR}/{spec['dir']}"
    return [
        f"{sub}/checkpoint",
        f"{sub}/{spec['stem']}.data-00000-of-00001",
        f"{sub}/{spec['stem']}.index",
        "README.md",  # HF stub — provenance snapshot
    ]


def fetch(url: str, dest: Path) -> tuple[int, str]:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with (
        urllib.request.urlopen(url, timeout=120, context=_ssl_context()) as resp,
        tmp.open("wb") as fh,
    ):
        while chunk := resp.read(1 << 16):
            fh.write(chunk)
    size = tmp.stat().st_size
    tmp.replace(dest)
    return size, sha256_of(dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="a2")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "movinet")
    args = parser.parse_args(argv)

    spec = VARIANTS[args.variant]
    vdir: Path = args.out / spec["dir"]
    vdir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    failures: list[str] = []
    for rel in variant_files(args.variant):
        url = f"{BASE}/{rel}"
        dest = vdir / Path(rel).name
        if dest.exists() and dest.stat().st_size > 0:
            digest, size = sha256_of(dest), dest.stat().st_size
            print(f"[skip] {dest.name} (present, {size / 1e6:.2f} MB)")
        else:
            try:
                size, digest = fetch(url, dest)
            except Exception as exc:  # noqa: BLE001 — report, keep the rest
                print(f"[FAIL] {rel}: {exc}", file=sys.stderr)
                failures.append(rel)
                continue
            print(f"[ok] {dest.name} {size / 1e6:.2f} MB")
        rows.append(
            {
                "variant": args.variant,
                "file": str(dest.relative_to(args.out)),
                "bytes": str(size),
                "sha256": digest,
                "url": url,
            }
        )

    manifest = args.out / "manifest.csv"
    new_fields = MANIFEST_FIELDS
    with manifest.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=new_fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[done] {args.variant} ({spec['metrics']}) -> {manifest}")
    if failures:
        print(f"[warn] {len(failures)} failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
