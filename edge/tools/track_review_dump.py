"""Phase 2 visual-review evidence generator (one deterministic pass).

Runs a tracker config over the bundled real crowd clip, spools annotated
frames, derives reassociation + internal-gap events from the SAME pass, and
copies only the frames around each event into ``<out>/review/`` — the evidence
pack for the Gate 2 manual overlay review (plan §7).

Usage (from edge/):
  uv run python tools/track_review_dump.py --out runs/phase2-review

Output layout:
  review/           selected frames, named ev{i}_{kind}_{ids}_f{frame}.jpg
  events.json       machine-readable event list (boxes + frame numbers)
  summary.json      fragmentation report (gate metric re-verification)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

EDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EDGE))
sys.path.insert(0, str(EDGE / "tools"))

import cv2  # noqa: E402
from track_stats import Sample, compute_fragmentation, iou  # noqa: E402

from mobisentra.vision.tracker import DetectorTracker  # noqa: E402


def find_reassociation_events(
    track_samples: dict[int, list[Sample]], gap_frames: int = 45, iou_thresh: float = 0.3
) -> list[dict]:
    """Same rule as track_stats.count_reassociations, but returns details."""
    ends = sorted((s[-1].frame, s[-1].bbox, tid) for tid, s in track_samples.items() if s)
    starts = sorted((s[0].frame, s[0].bbox, tid) for tid, s in track_samples.items() if s)
    events: list[dict] = []
    for end_frame, end_box, end_id in ends:
        for start_frame, start_box, start_id in starts:
            if start_id == end_id or start_frame < end_frame:
                continue
            if start_frame - end_frame > gap_frames:
                break
            if start_frame > end_frame and iou(end_box, start_box) >= iou_thresh:
                events.append(
                    {
                        "kind": "reassociation",
                        "old_id": end_id,
                        "new_id": start_id,
                        "last_frame_old": end_frame,
                        "first_frame_new": start_frame,
                        "last_box_old": end_box,
                        "first_box_new": start_box,
                    }
                )
                break
    return events


def find_gap_events(
    track_samples: dict[int, list[Sample]], fps: float, min_gap_frames: int = 20
) -> list[dict]:
    """Stable track (>=5 s of samples) that vanishes and comes back."""
    min_samples = int(5.0 * fps)
    events: list[dict] = []
    for tid, samples in track_samples.items():
        if len(samples) < min_samples:
            continue
        for i in range(1, len(samples)):
            prev, nxt = samples[i - 1], samples[i]
            gap = nxt.frame - prev.frame
            if gap >= min_gap_frames:
                events.append(
                    {
                        "kind": "gap",
                        "track_id": tid,
                        "last_frame_before": prev.frame,
                        "first_frame_after": nxt.frame,
                        "gap_seconds": round(gap / fps, 2),
                        "box_before": prev.bbox,
                        "box_after": nxt.bbox,
                    }
                )
                break  # first gap per track is enough for review
    events.sort(key=lambda e: e["last_frame_before"])
    return events


def frames_around(anchor_frames: list[int], last_frame: int, pad: int = 3) -> list[int]:
    want: set[int] = set()
    for f in anchor_frames:
        for d in (-pad, 0, pad):
            g = f + d
            if 0 <= g <= last_frame:
                want.add(g)
    return sorted(want)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path,
                        default=EDGE / "sample_data" / "videos" / "crowd_real_01.mp4")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--conf", type=float, default=0.3)
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--tracker", default="configs/botsort-tuned.yaml")
    args = parser.parse_args()

    out: Path = args.out
    spool, review = out / "spool", out / "review"
    for d in (out, spool, review):
        d.mkdir(parents=True, exist_ok=True)

    detector = DetectorTracker(
        model=args.model,
        conf=args.conf,
        classes=[0],
        imgsz=640,
        tracker=args.tracker,
        track_buffer=None,  # respect the yaml
        device="auto",
    )
    print(f"[dump] device={detector.device} tracker={args.tracker}")

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    track_samples: dict[int, list[Sample]] = {}
    frame_idx = 0
    while True:
        ok, image = cap.read()
        if not ok:
            break
        for p in detector.process_frame(image):
            track_samples.setdefault(p.track_id, []).append(
                Sample(frame=frame_idx, bbox=p.bbox)
            )
            x1, y1, x2, y2 = (int(v) for v in p.bbox)
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                image, f"#{p.track_id}", (x1, max(12, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2,
            )
        cv2.imwrite(str(spool / f"f{frame_idx:05d}.jpg"), image,
                    [cv2.IMWRITE_JPEG_QUALITY, 80])
        frame_idx += 1
        if frame_idx % 150 == 0:
            print(f"  …frame {frame_idx}")
    cap.release()
    last_frame = frame_idx - 1
    print(f"[dump] frames={frame_idx} fps={fps:.2f}")

    report = compute_fragmentation(track_samples, fps, 10.0)
    re_events = find_reassociation_events(track_samples)
    gap_events = find_gap_events(track_samples, fps)
    print(
        f"[dump] stable={report.stable_ratio:.3f} "
        f"flicker_filtered={report.stable_ratio_flicker_filtered:.3f} "
        f"tracks={report.n_tracks} stable_tracks={report.n_stable_tracks} "
        f"reassoc={len(re_events)} gaps={len(gap_events)}"
    )

    all_events = re_events + gap_events
    for i, ev in enumerate(all_events):
        anchors = (
            [ev["last_frame_old"], ev["first_frame_new"]]
            if ev["kind"] == "reassociation"
            else [ev["last_frame_before"], ev["first_frame_after"]]
        )
        ev["review_frames"] = frames_around(anchors, last_frame)
        tag = (
            f"ev{i}_switch_{ev['old_id']}to{ev['new_id']}"
            if ev["kind"] == "reassociation"
            else f"ev{i}_gap_{ev['track_id']}"
        )
        for f in ev["review_frames"]:
            shutil.copy2(spool / f"f{f:05d}.jpg", review / f"{tag}_f{f}.jpg")

    summary = {
        "video": args.video.name,
        "frames": frame_idx,
        "fps": fps,
        "device": detector.device,
        "tracker": args.tracker,
        "stable_ratio": round(report.stable_ratio, 3),
        "stable_ratio_flicker_filtered": round(report.stable_ratio_flicker_filtered, 3),
        "person_seconds": round(report.person_seconds, 1),
        "n_tracks": report.n_tracks,
        "n_stable_tracks": report.n_stable_tracks,
        "lifetime_histogram": report.lifetime_histogram,
        "n_reassociations": len(re_events),
        "n_gaps": len(gap_events),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "events.json").write_text(json.dumps(all_events, indent=2, default=list))
    shutil.rmtree(spool)
    print(f"[dump] review frames: {len(list(review.iterdir()))} -> {review}")


if __name__ == "__main__":
    main()
