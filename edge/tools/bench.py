"""Sustained-FPS benchmark (Phase 2 gate, plan §2.2).

Measures full-pipeline throughput (decode + detect + track) over a sustained
window on a synthetic clip at each requested resolution. Synthetic content is
deliberate: decode+inference load is content-independent (plan §4).

Usage:
  uv run python tools/bench.py --seconds 60 --resolutions 1280x720 1920x1080
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def bench_resolution(video: Path, seconds: float, process) -> dict:
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    started = None
    frames = 0
    warmup = 5
    warm = 0
    try:
        while True:
            ok, image = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            if warm < warmup:
                warm += 1
                continue
            if started is None:
                started = time.monotonic()
            process(image)
            frames += 1
            elapsed = time.monotonic() - started
            if elapsed >= seconds:
                return {
                    "seconds": round(elapsed, 2),
                    "frames": frames,
                    "fps": round(frames / elapsed, 2),
                }
    finally:
        cap.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--video", type=Path, default=Path("sample_data/videos/bus_interior_01.mp4")
    )
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--resolutions", nargs="+", default=["1280x720", "1920x1080"])
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--conf", type=float, default=0.3)
    parser.add_argument("--tracker", default="configs/tracktrack-tuned.yaml")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    from mobisentra.vision.tracker import DetectorTracker, resolve_device

    device = resolve_device("auto")
    print(f"[bench] model={args.model} device={device} video={args.video.name}")
    results = {
        "model": args.model,
        "device": device,
        "sustained_seconds": args.seconds,
        "resolutions": {},
    }
    for res in args.resolutions:
        w, h = (int(v) for v in res.split("x"))
        detector = DetectorTracker(
            model=args.model, conf=args.conf, classes=[0], tracker=args.tracker
        )

        def process(image, detector=detector, w=w, h=h):
            import cv2 as _cv2

            if image.shape[1] != w or image.shape[0] != h:
                image = _cv2.resize(image, (w, h))
            detector.process_frame(image)

        stats = bench_resolution(args.video, args.seconds, process)
        results["resolutions"][res] = stats
        print(
            f"[bench] {res}: {stats['fps']} fps over {stats['seconds']}s ({stats['frames']} frames)"
        )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=2) + "\n")
        print(f"[bench] results -> {args.out}")


if __name__ == "__main__":
    main()
