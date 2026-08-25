# Phase 2 — Detection + Tracking · Plan

> **Status: EXECUTED 2026-08-25/26 — see [`phase-2-completion.md`](./phase-2-completion.md)**
> **Gate 2: PARTIAL — FPS ✅, clone ✅, ID metric 0.741 vs ≥ 0.80 (owner decision pending)**
> Source of truth: [`implementation-sequence.md`](./implementation-sequence.md) → Phase 2 (on conflict, runbook + `implementation-plan.md` win)
> Duration: ~1.5 weeks · Budget: 6 build days + 1 gate/doc day
> Prerequisite: Gate 1 ✅ (2026-08-25)

---

## 1. Objective

Turn frames into **stable person track IDs per camera** — the substrate every analytic in Phases 3–5 consumes:

> `Frame → YOLO26n + ByteTrack → [(track_id, bbox, conf)] → track history`

## 2. Gate 2 (from runbook, made measurable)

| # | Criterion | Measured by |
|---|---|---|
| 1 | Stable track IDs through partial occlusions on crowded footage | ID-fragmentation metric (§7) on bundled real crowd clip: **≥ 80% of person-seconds belong to tracks living ≥ 10 s** + manual overlay review confirms no ID swap between persistent people |
| 2 | ≥ 15 FPS sustained on laptop | `tools/bench.py` at 720p **and** 1080p, device recorded, 60 s sustained window |
| 3 | Clone test re-run | fresh clone → `uv sync` → quickstart compose still works |

## 3. Model Decision — YOLO26n (updates the locked table)

Verified against Ultralytics docs (2026-08-25 session):

| | YOLO11n | YOLO26n | Why it matters to us |
|---|---|---|---|
| COCO mAP 50-95 | 39.5 | **40.9** | more detections in crowded frames |
| CPU ONNX latency | 56.1 ms | **38.9 ms (−31%)** | edge-first, Jetson path (Phase 10) |
| T4 TensorRT | 1.5 ms | 1.7 ms | parity — irrelevant at our scale |
| Params / FLOPs | 2.6M / 6.5B | **2.4M / 5.4B** | smaller |
| Pose variant | yes | **yes, +RLE loss (up to +7.2 AP)** | Phase 4 fall detection |
| NMS | required | **none (end-to-end)** | no NMS knob; deterministic latency; simpler exports |

- Same framework (`ultralytics`), same API, same AGPL license — model name is one config value.
- **Fallback:** if any YOLO26-specific blocker appears, `yolo11n.pt` is a one-line config change; wrapper must be model-agnostic from day one.
- On approval, `implementation-plan.md` §2 decision row and runbook Step 2.2 snippet get updated to YOLO26n.

## 4. Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Wrapper shape | Thin `DetectorTracker` class: `process_frame(frame) -> list[TrackedPerson]` | ultralytics stays swappable; downstream never touches raw Results |
| Tracking call | `model.track(frame, persist=True, tracker="bytetrack.yaml", conf=cfg, classes=[0], verbose=False)` | per runbook; person class only |
| Device | Auto-detect (CUDA → MPS → CPU) + `MOBISENTRA_DEVICE` env override | dev machine is Apple Silicon (MPS); Jetson later (CUDA); bench must record device |
| Config | `configs/detection.yaml` (global): model, conf=0.3, tracker cfg, track_buffer=60, classes=[0], imgsz=640, device | one place to tune/A-B; per-camera override deferred |
| Track history | `TrackHistory`: per-camera `track_id → deque[(ts, bbox, center, conf)]`, ~10 s capacity, staleness purge | pure logic, unit-testable without ultralytics; feeds zones (P3), fall (P4), fight (P5) |
| Testability split | Pure modules (history, postprocess) tested in CI; model tests `importorskip("ultralytics")` + skip when weights unavailable | keeps CI fast (no torch download in CI) |
| FPS benchmark content | Synthetic clips (decode load is content-independent) | real clip only for detection/ID quality — not FPS |
| Overlay + debug JSONL | `--detect --preview` draws boxes+IDs; JSONL per-frame detections to `runs/debug/` (gitignored) | per runbook Step 2.6; runs/ already ignored |
| Non-person classes | Logged, not tracked | per runbook Step 2.4 (bags later) |

## 5. Module Plan

```
edge/mobisentra/vision/
├── tracker.py          # DetectorTracker wrapper (ultralytics), TrackedPerson dataclass,
│                       #   results→dataclass postprocess (mockable)
└── track_history.py    # TrackHistory ring buffer + purge (pure logic)
edge/configs/detection.yaml
edge/tools/bench.py     # sustained-FPS benchmark: resolution sweep, device, JSON summary
edge/tools/track_stats.py  # ID-fragmentation metric over a clip (gate evidence)
edge/mobisentra/main.py # extended: --detect flag wires tracker + history + overlay + JSONL
edge/tests/
├── test_track_history.py    # pure: append/cap/purge/history-window queries
├── test_tracker_postprocess.py  # fake Results objects → TrackedPerson list
└── test_detection_smoke.py  # importorskip(ultralytics); real clip → ≥1 person tracked,
                             #   IDs persist across consecutive frames
```

Dependencies added (Step 2.1): `ultralytics`, `supervision` (supervision actually consumed in Phase 3; added now per runbook).

## 6. Test Data — the synthetic-clip problem

COCO-pretrained YOLO does **not** detect the colored rectangles in our synthetic clips (correctly — they're not people). Consequence, handled explicitly:

| Need | Source | Bundled? |
|---|---|---|
| FPS benchmark (decode + inference load) | existing synthetic clips | ✅ already in repo |
| Detection + tracking quality (gate) | **1–2 real crowd clips from Pexels** (open license): crowd/subway scale, no close-up faces — privacy rule for bundled samples recorded in `SOURCES.md` with attribution | ✅ bundled (~≤5 MB each) |
| Optional deeper tracker eval | MOT17 via download script — **NC license: never bundled, local eval only** | ❌ script only, deferred unless gate metric proves insufficient |

## 7. Gate Metric — ID fragmentation (no ground truth needed)

On the real crowd clip, with N persistent visible people:
- `person_seconds = Σ track lifetimes`
- `stable_ratio = person_seconds(tracks ≥ 10 s) / person_seconds(all)`
- **Pass:** `stable_ratio ≥ 0.80` and manual overlay review shows no ID transfer between two persistent people through an occlusion moment.

## 8. Schedule

| Day | Deliverable |
|---|---|
| 1 | Deps + `detection.yaml` + `DetectorTracker` + device auto-detect; weights download verified |
| 2 | `TrackHistory` + postprocess + pure tests green |
| 3 | `main.py --detect` wiring: overlay, JSONL sink, per-camera tracker instances (`persist=True` per camera!) |
| 4 | Pexels clip acquisition + `SOURCES.md`; detection smoke test on real clip green |
| 5 | Tuning: conf sweep, `track_buffer` 50/60/70, ByteTrack vs BoT-SORT A/B on clips; `track_stats.py` metric |
| 6 | `bench.py` numbers at 720p/1080p recorded (device noted); threshold decisions documented |
| 7 | Buffer; `phase-2-completion.md`; gate review with owner |

## 9. Risks

| Risk | Mitigation |
|---|---|
| MPS quirks on Apple Silicon (op fallbacks, FPS variance) | device override env; bench records device; `imgsz=480` fallback if 15 FPS missed at 1080p |
| YOLO26 + tracker-yaml naming/config drift | fallback = `yolo11n` one-liner; wrapper model-agnostic |
| CI bloat from torch/ultralytics | vision tests `importorskip`; CI installs stay light (no ultralytics in CI) |
| Real-clip privacy | crowd scale, no close-ups; attribution + license in `SOURCES.md`; project rule honored |
| Track history memory growth | bounded deque + purge on staleness (tested) |
| ID fragmentation metric gamed by too-low conf (merge) / too-high (fragment) | metric reported alongside conf choice; A/B table in completion doc |

## 10. Out of Scope (explicit)

- Zones/occupancy/door (Phase 3), pose (Phase 4), action recognition (Phase 5)
- Publishing detections over MQTT (Phase 7 — Phase 2 emits JSONL debug only)
- Fine-tuning on own footage (Phase 10 loop); TensorRT/Jetson export (Phase 10)
- MOT-style ground-truth metrics (MOTA/HOTA) — deferred unless the fragmentation metric proves insufficient

## 11. Approval

- [x] Owner reviews this plan (incl. YOLO26n switch, Pexels clip bundling) — approved 2026-08-25
- [x] → On approval: implementation starts Day 1; runbook + decision table updated to YOLO26n (2026-08-26)
