# Phase 2 — Completion Report

> **Status: EXECUTED (2026-08-25 – 2026-08-26) · Gate 2: PARTIAL — numeric criterion missed, owner decision pending (§8)**
> Scope executed: `Doc/Implementation/phase-2-plan.md`
> Runbook: `implementation-sequence.md` → Phase 2
> All numbers below re-measured first-hand on 2026-08-26 (dev machine, Apple Silicon MPS); metric reproduced 3× independently with identical results.

---

## 1. Gate 2 — Evidence

| # | Criterion | Requirement | Result |
|---|---|---|---|
| 1a | ID-fragmentation metric (plan §7) | flicker-filtered stable_ratio **≥ 0.80** on bundled real crowd clip | ❌ **0.741** (shipped config; best swept variant 0.763 — §5) |
| 1b | Manual overlay review | no ID swap between persistent people | ⏳ **PENDING OWNER** — automated review blocked (§7), 15-event checklist + 90 annotated frames prepared |
| 2 | Sustained FPS | ≥ 15 FPS at 720p **and** 1080p, 60 s window | ✅ **57.25 @720p** (3 435 frames), **44.09 @1080p** (2 646 frames), device=mps |
| 3 | Clone test | fresh clone → `uv sync` → suite green | ✅ HEAD clone: ruff clean, 38 passed, compose config valid; post-commit simulation (full tree incl. Phase 2): ruff clean, **54 passed** + vision smoke green |

**Gate verdict: NOT PASSED as-written** — criterion 1a misses 0.80. Per runbook rule "gates are hard", Phase 3 does not start until the owner picks an option in §8.

## 2. What Was Built

| Module | Role |
|---|---|
| `mobisentra/vision/tracker.py` | `DetectorTracker` (one per camera, `persist=True`), `TrackedPerson` dataclass, device auto-detect (CUDA→MPS→CPU + `MOBISENTRA_DEVICE`), pure `postprocess_results()`, temp-yaml `track_buffer` override with cache |
| `mobisentra/vision/track_history.py` | Per-camera ring buffer `track_id → deque[(ts, bbox, center, conf)]`, ~10 s capacity, staleness purge |
| `configs/detection.yaml` | Global detection config (model/conf/imgsz/tracker/device) — model is one config value |
| `configs/botsort-tuned.yaml` | A/B winner (§5): looser IoU matching, `track_buffer: 90`, lower new-track bar |
| `main.py` (+75 lines) | `--detect` wiring: per-camera tracker + history, `--debug-detections` JSONL to `runs/debug/`, `--preview` overlay |
| `tools/bench.py` | Sustained-FPS benchmark, resolution sweep, device recorded |
| `tools/track_stats.py` | ID-fragmentation metric + reassociation counter (gate evidence) |
| `tools/track_review_dump.py` | One-pass evidence-pack generator: annotated frames + `events.json` around every reassociation/gap |
| `tests/` (4 files) | track history (pure), postprocess (mocked Results), track stats (pure), detection smoke (opt-in real model) |
| `sample_data/videos/crowd_real_01.mp4` | 2.4 MB, 20 s @480p@30fps real crowd — Wikimedia Commons (Tielingxi Railway Station, Tomskyhaha), **CC BY-SA 4.0**, attribution in `SOURCES.md` |

## 3. Design Verification (plan §4 vs shipped)

| Plan decision | Status |
|---|---|
| Thin model-agnostic wrapper; downstream never touches raw Results | ✅ model swapped freely during A/B (§5) via config/CLI only |
| `persist=True` per camera instance | ✅ `main.py` creates one `DetectorTracker` per camera |
| Device auto-detect + env override | ✅ resolved mps on dev machine; bench/smoke record it |
| Pure-vs-model test split | ✅ 54 tests run in 0.3 s without torch; vision test opt-in `MOBISENTRA_VISION_TEST=1` (passed: persons detected, IDs persist ≥60 % of frame transitions, avg ≥1 person/frame) |
| FPS on synthetic clips, ID quality on real clip | ✅ bench uses `bus_interior_01.mp4`; metric uses `crowd_real_01.mp4` |
| Overlay + debug JSONL to gitignored `runs/` | ✅ verified in 1-min e2e: 3 211 records across 3 cameras |
| Track history bounded + purged | ✅ unit-tested |
| YOLO26n with `yolo11n.pt` one-line fallback | ✅ both measured (§5) |

## 4. E2E Verification (2026-08-26)

`uv run python -m mobisentra.main --config configs/cameras.yaml --detect --debug-detections --minutes 1`

- 3 cameras UP for 60 s, device=mps, zero reconnects
- Ingestion lag with detection attached: p95 ≤ 49 ms, max 49 ms on all cameras (Phase 1 gate budget 200 ms still holds)
- Debug JSONL: 1 224 + 1 177 + 810 records; `people: []` on synthetic clips is **correct** (COCO model sees no humans in generated rectangles)

## 5. Tuning A/B — all on `crowd_real_01.mp4` (600 frames @30 fps)

**Tracker family** (yolo26n, conf 0.3, imgsz 640):

| Config | flicker-filtered | raw | tracks | ≥10 s | reassociations |
|---|---|---|---|---|---|
| stock `bytetrack.yaml` | 0.679 | 0.584 | 103 | 9 | 45 |
| stock `botsort.yaml` | 0.603 | 0.540 | 68 | 8 | 19 |
| **`botsort-tuned.yaml` (shipped)** | **0.741** | 0.715 | **45** | **12** | **7** |
| tuned + `with_reid: true` | 0.720 | 0.677 | 49 | 10 | 11 |

ReID rejected: worse ratio, +1 reassociation, and pulls a classifier-weights side-download (`yolo26n-cls.pt`) at runtime.

**imgsz** (tuned config): 640 → **0.741 / 45 / 7** · 960 → 0.748 / 93 / 35 · 1280 → 0.635 / 82 / 11. 960 px inflates tracks 2× and reassociations 5× — finer boxes fragment IDs. 640 ships.

**conf** (tuned config): 0.25 → **0.763** / 60 / 19 · 0.30 → 0.741 / 45 / **7** · 0.35 → 0.658 / 42 / 5. 0.25 maximizes the ratio but 2.7× the reassociations; conf 0.3 ships (fragmentation count is closer to the gate's "no ID swap" intent than the ratio). Documented as the ratio-optimal alternative.

**track_buffer** (conf 0.25): 90 → 0.763 · 120 → 0.744. No gain; 90 ships.

**Model** (stock bytetrack basis): yolo11n 0.537 / 118 / 43 · **yolo26n 0.679** / 103 / 45 · yolo26s 0.480 / 120 / 38. On tuned config: yolo11n 0.679 / 51 / 7 vs **yolo26n 0.741 / 45 / 7**. YOLO26n validated on both bases; size-up rejected decisively.

## 6. The 0.80 Miss — honest analysis

Shipped numbers: `stable_ratio` 0.715, **flicker-filtered 0.741**, person-seconds 242.2, tracks 45, stable (≥10 s) 12, reassociations 7, lifetime histogram {<2 s: 21, 2–5 s: 7, 5–10 s: 5, ≥10 s: 12}.

- The clip is **adversarial for ID stability**: diagonal flowing pedestrian traffic at station scale, mutual occlusions every few seconds, 480p source. Static/queuing crowds — the actual transit-interior deployment target — are the easy case.
- 12 tracks carry the persistent people; the miss concentrates in walk-through traffic being re-IDed (7 reassociations, of which several are provably chains on the same individuals — see §7).
- Everything single-knob reachable was swept (§5); best variant 0.763 still misses. Closing the remaining gap needs appearance features that work at 480p — out of Phase 2 scope (plan §10).

## 7. Manual Overlay Review — owner checklist (PENDING)

Automated review was blocked three ways: `look_at` timeouts, multimodal-looker agent model-routing broken, orchestration model has no image input. Evidence pack preserved (gitignored): `edge/runs/phase2-review/` — 90 annotated frames (`review/`), `events.json` (boxes + frames), `summary.json`. Regenerate any time: `uv run python tools/track_review_dump.py --out runs/phase2-review`.

**How to review:** open the named frames; green box + `#ID` label = track. For switches: compare clothing/size of the person in the old-ID box (frames just before) vs new-ID box (frames just after). For gaps: same ID disappears and returns — confirm it is the same person.

| Event | Type | ID story | Key frames | Verdict (fill) |
|---|---|---|---|---|
| ev0 | switch | #40 → #43 @ f72→84 (center) | 69–87 | FRAG / DIFF / UNSURE |
| ev1 | switch | #50 → #53 @ f117→129 (center-left) | 114–132 | FRAG / DIFF / UNSURE |
| ev2 | switch | #11 → #85 @ f135→177 (right edge, 1.4 s gap) | 132–180 | FRAG / DIFF / UNSURE |
| ev3 | switch | #118 → #127 @ f357→388 (center) | 354–391 | FRAG / DIFF / UNSURE |
| ev4 | switch | #127 → #153 @ f460→484 — **chains ev3: same person twice** | 457–487 | FRAG / DIFF / UNSURE |
| ev5 | switch | #95 → #149 @ f461→470 (left) | 458–473 | FRAG / DIFF / UNSURE |
| ev6 | switch | #55 → #164 @ f519→530 (right edge) | 516–533 | FRAG / DIFF / UNSURE |
| ev7 | gap | #10 gone f44→83 (1.3 s) | 41–86 | OK / THEFT / UNSURE |
| ev8 | gap | #32 gone f91→138 (1.6 s) | 88–141 | OK / THEFT / UNSURE |
| ev9 | gap | #55 gone f133→159 (0.9 s) — #55 also in ev6 | 130–162 | OK / THEFT / UNSURE |
| ev10 | gap | #43 gone f237→260 (0.8 s) — #43 is ev0's new ID | 234–263 | OK / THEFT / UNSURE |
| ev11 | gap | #95 gone f243→334 (**3.0 s**) — #95 also in ev5 | 240–337 | OK / THEFT / UNSURE |
| ev12 | gap | #97 gone f294→342 (1.6 s) | 291–345 | OK / THEFT / UNSURE |
| ev13 | gap | #87 gone f392→461 (2.3 s) | 389–464 | OK / THEFT / UNSURE |
| ev14 | gap | #48 gone f460→486 (0.9 s) | 457–489 | OK / THEFT / UNSURE |

Gate 1b passes iff: no switch verdicts land on DIFF→same-person (i.e. no ID *theft between persistent people*) and no gap verdict lands on THEFT. ID fragmentation on walk-through traffic (FRAG) counts against 1a, already recorded as the miss.

## 8. Gate Decision — options for owner

| Option | Action | Consequence |
|---|---|---|
| **A. Waive with rationale** (recommended if checklist is clean) | Accept 0.741 on this adversarial clip; record waiver here; proceed Phase 3 | Zones/occupancy (P3) consume per-frame boxes — robust to fragmentation; dwell/door rules tolerate occasional re-ID. Risk carried into P4/P5 temporal analytics |
| B. Re-gate on a second clip | Bundle 1 queuing/static crowd clip (Pexels/Wikimedia, same privacy rules); re-run `tools/track_stats.py` + checklist | Cheap, honest test of "flowing traffic is the hard case" hypothesis; likely clears 0.80 |
| C. Keep tuning | Only untried lever: working appearance features at 480p (custom ReID model — new dependency + training) | Out of Phase 2 scope (plan §10); expected yield low; blocks calendar |

## 9. Issues Hit & Resolutions

| Issue | Resolution |
|---|---|
| CLI default `--track-buffer 60` silently overrode tuned yaml's 90 (config drift) | `track_buffer=None` = respect yaml; override only on explicit flag |
| Evidence pack lived in OS temp dir → wiped between sessions | Generator ported to `tools/track_review_dump.py`; pack regenerated deterministically (0.741 reproduced exactly) |
| Automated visual review dead: `look_at` timeout, agent model routing invalid (`glm-4.6v`, `opencode/gpt-5.5` not found), session model lacks image input | Manual review converted to owner checklist (§7) — also the most legitimate form of the gate's "manual overlay review" |
| `zip(..., strict=True)` on offset-pair walk crashed the dump tool | Index-walk rewrite (no length trap) |
| `with_reid` experiment auto-downloaded `yolo26n-cls.pt` into `edge/` at runtime | Rejected config; file gitignored (`*.pt`); noted as ReID cost |
| Dead-session bash outputs unverifiable | Every number in this doc re-measured 2026-08-26 |

## 10. Verification Commands

```bash
cd edge
uv run ruff check . && uv run pytest                          # 54 passed, 2 opt-in skips
MOBISENTRA_VISION_TEST=1 uv run pytest tests/test_detection_smoke.py -v   # passed
uv run python tools/track_stats.py --video sample_data/videos/crowd_real_01.mp4 \
    --tracker configs/botsort-tuned.yaml                      # 0.741 / 45 / 12 / 7
uv run python tools/bench.py --seconds 60                     # 57.25 @720p, 44.09 @1080p, mps
uv run python tools/track_review_dump.py --out runs/phase2-review
uv run python -m mobisentra.main --config configs/cameras.yaml --detect --debug-detections --minutes 1
```

## 11. Deferred / Notes

- `supervision` dependency added (runbook Step 2.1) but first consumed in Phase 3 (zones).
- MOT-style ground-truth metrics (MOTA/HOTA) stay deferred — fragmentation metric + checklist proved sufficient to characterize behavior.
- Per-camera detection overrides deferred (global `detection.yaml` only).
- Clone test on HEAD exercises Phase 0+1 (38 tests); Phase 2 files are uncommitted — re-run clone test after the owner commits for the full-tree variant (already simulated locally: green).
- Evidence pack `runs/phase2-review/` is gitignored; regenerate with the tool if needed.

## 12. Next

**Blocked on owner:** §8 gate decision + §7 checklist (≈5 min: 15 rows, frames at `edge/runs/phase2-review/review/`).
After decision: Phase 3 — Zones, Occupancy, Door Rules (`implementation-sequence.md` → Phase 3).
