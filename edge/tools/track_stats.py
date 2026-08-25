"""ID-fragmentation metric over a clip (Phase 2 gate, plan §7).

person_seconds  = sum of track lifetimes (frames / fps)
stable_ratio    = person_seconds of tracks >= min_lifetime / person_seconds all
Pass: stable_ratio >= 0.80 (plus manual overlay review, recorded separately).

Usage:
  uv run python tools/track_stats.py --video sample_data/videos/crowd_real_01.mp4 \
      [--conf 0.3] [--track-buffer 60] [--tracker bytetrack.yaml] [--min-lifetime 10]
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FragmentationReport:
    stable_ratio: float
    stable_ratio_flicker_filtered: float
    person_seconds: float
    n_tracks: int
    n_stable_tracks: int
    lifetime_histogram: dict[str, int]


def compute_fragmentation(
    track_frames: dict[int, list[int]], fps: float, min_lifetime_s: float
) -> FragmentationReport:
    min_frames = min_lifetime_s * fps
    flicker_frames_limit = 2.0 * fps
    total_frames = sum(len(v) for v in track_frames.values())
    stable_frames = sum(len(v) for v in track_frames.values() if len(v) >= min_frames)
    non_flicker_frames = sum(
        len(v) for v in track_frames.values() if len(v) >= flicker_frames_limit
    )
    person_seconds = total_frames / fps
    buckets = {"<2s": 0, "2-5s": 0, "5-10s": 0, ">=10s": 0}
    for frames in track_frames.values():
        lifetime = len(frames) / fps
        if lifetime < 2:
            buckets["<2s"] += 1
        elif lifetime < 5:
            buckets["2-5s"] += 1
        elif lifetime < 10:
            buckets["5-10s"] += 1
        else:
            buckets[">=10s"] += 1
    return FragmentationReport(
        stable_ratio=stable_frames / total_frames if total_frames else 0.0,
        stable_ratio_flicker_filtered=(
            stable_frames / non_flicker_frames if non_flicker_frames else 0.0
        ),
        person_seconds=person_seconds,
        n_tracks=len(track_frames),
        n_stable_tracks=sum(1 for v in track_frames.values() if len(v) >= min_frames),
        lifetime_histogram=buckets,
    )


@dataclass(frozen=True)
class Sample:
    frame: int
    bbox: tuple[float, float, float, float]


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


def count_reassociations(
    track_samples: dict[int, list[Sample]], gap_frames: int = 45, iou_thresh: float = 0.3
) -> int:
    """Suspected ID fragmentations: a track ends and a *new* track starts
    within ``gap_frames`` with box IoU >= ``iou_thresh`` — i.e. somebody in
    the same place got a fresh ID. Walk-through traffic never triggers this.
    """
    ends = sorted(
        (samples[-1].frame, samples[-1].bbox, track_id)
        for track_id, samples in track_samples.items()
        if samples
    )
    starts = sorted(
        (samples[0].frame, samples[0].bbox, track_id)
        for track_id, samples in track_samples.items()
        if samples
    )
    events = 0
    for end_frame, end_box, end_id in ends:
        for start_frame, start_box, start_id in starts:
            if start_id == end_id or start_frame < end_frame:
                continue
            if start_frame - end_frame > gap_frames:
                break
            if start_frame > end_frame and iou(end_box, start_box) >= iou_thresh:
                events += 1
                break
    return events


def scan_clip(
    video: Path,
    process: Callable[[object], list],
    progress_every: int = 0,
) -> tuple[dict[int, list[Sample]], float]:
    import cv2

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    track_samples: dict[int, list[Sample]] = {}
    frame_idx = 0
    while True:
        ok, image = cap.read()
        if not ok:
            break
        for p in process(image):
            track_samples.setdefault(p.track_id, []).append(
                Sample(frame=frame_idx, bbox=p.bbox)
            )
        frame_idx += 1
        if progress_every and frame_idx % progress_every == 0:
            print(f"  …frame {frame_idx}")
    cap.release()
    return track_samples, fps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--conf", type=float, default=0.3)
    parser.add_argument("--track-buffer", type=int, default=None,
                        help="override track_buffer (default: keep the yaml's value)")
    parser.add_argument("--tracker", default="bytetrack.yaml")
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--min-lifetime", type=float, default=10.0)
    args = parser.parse_args()

    from mobisentra.vision.tracker import DetectorTracker

    detector = DetectorTracker(
        model=args.model,
        conf=args.conf,
        classes=[0],
        tracker=args.tracker,
        track_buffer=args.track_buffer,
        imgsz=args.imgsz,
    )
    print(f"[track_stats] {args.video.name} conf={args.conf} "
          f"tracker={args.tracker} buffer={args.track_buffer}")
    track_samples, fps = scan_clip(args.video, detector.process_frame)
    report = compute_fragmentation(track_samples, fps, args.min_lifetime)
    reassociations = count_reassociations(track_samples)

    verdict = "PASS" if report.stable_ratio_flicker_filtered >= 0.80 else "FAIL"
    print(f"[track_stats] stable_ratio={report.stable_ratio:.3f} "
          f"flicker_filtered={report.stable_ratio_flicker_filtered:.3f} "
          f"({verdict}, gate >= 0.80 on flicker-filtered)")
    print(f"[track_stats] person_seconds={report.person_seconds:.1f} "
          f"tracks={report.n_tracks} stable={report.n_stable_tracks}")
    print(f"[track_stats] suspected_reassociations={reassociations} "
          f"(ID fragmentations: track ends, new track starts same place <=1.5s)")
    print(f"[track_stats] lifetimes: {report.lifetime_histogram}")


if __name__ == "__main__":
    main()
