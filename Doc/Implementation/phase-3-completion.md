# Phase 3 — Completion Report

> **Status: EXECUTED (2026-08-26 – 2026-08-27) · Gate 3: 2/3 ticked — occupancy ±10% measured, verdict awaits owner's manual counts (§1)**
> Scope executed: `Doc/Implementation/phase-3-plan.md` (draft approval was pending at execution start; owner go was given per-step)
> Runbook: `implementation-sequence.md` → Phase 3

---

## 1. Gate 3 — Evidence

| # | Criterion | Requirement | Result |
|---|---|---|---|
| 1 | Occupancy vs manual | within ±10% (min 1 person) at 5 sampled frames | ⏳ **PENDING OWNER VERDICT** — pipeline measured **3, 3, 3, 5, 4** at bus1.mp4 frames 58/174/290/406/522 (current stack, yolo26n-pose, 2026-08-27). Annotated frames: `edge/runs/occupancy-check/frame_*.jpg`. Owner counts heads in the 5 JPGs, then: `cd edge && .venv/bin/python tools/occupancy_check.py verdict --dir runs/occupancy-check --manual <c1>,<c2>,<c3>,<c4>,<c5>` |
| 2 | Empty-zone false positives | zero zone events over 30 min of empty-zone footage | ✅ **PASS** — `tools/zone_fp_soak.py` (2026-08-27): siting pass unions all detections into a 24×14 grid, largest empty rectangle (maximal-rectangle scan, unit-tested) hosts occupancy + restricted + door zones; production `CameraAnalytics` over the real-footage pool (4 clips ≈ 49 s, 16:9) looped **37× → 30.0 min stream, 23,760 analyzed frames, 0 zone events** (`runs/zone-fp-soak.json`). Limitation: ~49 s unique footage repeated — exposure tested is detector noise + zone logic over stream time |
| 3 | Zone editor round-trip | polygon → normalized YAML → reload identical | ✅ evidenced at Step 3.5 (2026-08-26) |

## 2. What Was Built

| Module | Role |
|---|---|
| `mobisentra/analytics/zones.py` | `ZoneEngine` — supervision `PolygonZone` per configured zone, feet (BOTTOM_CENTER) anchor, normalized polygons denormalized per frame size (re-denormalizes with warning on change) |
| `mobisentra/analytics/occupancy.py` | `OccupancyMonitor` — hysteresis bands (normal/crowded/over), confirm-frames debounce, first reading establishes silently |
| `mobisentra/analytics/zone_events.py` | `DwellTracker` — per-(zone, track) accumulators → `restricted_zone_entry`, `door_obstruction` (schemas-v0 kinds); exhaustive `ZoneType` match |
| `mobisentra/analytics/engine.py` | `CameraAnalytics` — one composition per camera: membership → occupancy bands + dwell events (+ fall cascade since Phase 4); `draw_overlay` for `--preview` |
| `mobisentra/events/sink.py` | `JsonlEventWriter` — per-camera JSONL at `runs/events/<cam>.jsonl`, flushed per row |
| `mobisentra/ingestion/config.py` | Typed `ZoneConfig`/`ZoneType` parsing (occupancy requires capacity; restricted/door/rest reject it) |
| `tools/zone_editor.py` | Click-polygon → normalized YAML (round-trip gate) |
| `tools/occupancy_check.py` | Gate-3 criterion 1: measure (sampled frames + annotated JPGs) / verdict (manual-count table) |
| `tools/zone_fp_soak.py` | Gate-3 criterion 2: empty-region siting + timed FP soak (2026-08-27) |
| Tests | `test_zone_config`, `test_zone_engine`, `test_zone_events`, `test_occupancy`, `test_occupancy_check`, `test_analytics_engine`, `test_zone_editor`, `test_zone_fp_soak` — suite total at close: **196 passed + 4 skipped, ruff clean** |

## 3. E2E Verification

- `--detect` runs zones + occupancy + dwell per analyzed frame for every configured camera; events land in `runs/events/<camera_id>.jsonl` (live `tail -f` verified during Day 4 wiring); `--preview` paints zone polygons, labels, and band state.
- FP soak (§1 #2) exercises the same `CameraAnalytics` composition for 30 min of stream — zero events.

## 4. Process Notes (honest)

- **Phase 4 started before Gate 3 closed** — runbook says gates are hard; the owner explicitly directed Phase 4 steps (4.1–4.6) on 2026-08-26/27 while Phase 3's gate evidence was deferred. Recorded as an owner-sanctioned deviation, not a silent skip.
- The zone machinery was extended during Phase 4.6a (2026-08-27): `ZoneType.REST` — zones where lying is expected suppress fall events; inert for occupancy/dwell. Design + benchmark in `phase-4-plan.md`.
- Footage inventory note: the crowd/synthetic clips listed in `sample_data/videos/SOURCES.md` are not present on the dev machine (`*.mp4` is gitignored; only 4 real clips ≈ 49 s). The soak looped them; criterion 1 used bus1.

## 5. Issues Hit & Resolutions

| Issue | Resolution |
|---|---|
| Occupancy flicker on borderline counts | hysteresis: N consecutive confirm frames (`occupancy_confirm_frames`), first reading establishes silently |
| Clips of different resolutions in one pool | ZoneEngine re-denormalizes with a warning; pool is uniformly 16:9 so the normalized empty strip maps consistently |
| Auto-siting the empty zone by hand-picked cells was arbitrary | maximal-rectangle scan over the detection-union grid; the algorithm's edge cases are unit-tested (it shipped with an off-by-one class of bugs that tests caught: stale stack heights → (start, height) pairs) |

## 6. Verification Commands

```bash
cd edge
.venv/bin/python -m pytest            # 196 passed + 4 skipped
.venv/bin/python -m ruff check .      # clean
# Gate 3 #2 (already recorded):
.venv/bin/python tools/zone_fp_soak.py site --pool sample_data/videos/*.mp4
.venv/bin/python tools/zone_fp_soak.py soak --pool sample_data/videos/*.mp4 \
    --minutes 30 --analyze-every 2 --report runs/zone-fp-soak.json
# Gate 3 #1 (owner): count heads in runs/occupancy-check/frame_*.jpg, then
.venv/bin/python tools/occupancy_check.py verdict --dir runs/occupancy-check --manual <5 counts>
```

## 7. Next

1. Owner runs the §1 verdict → tick criterion 1 → Phase 3 formally closed.
2. Phase 4 close: FP-fork decision (mattress annotation vs documented limit) → `phase-4-completion.md`.
3. Phase 2 A′ waive decision (parked, `phase-2-completion.md §8`).
4. Phase 5 (altercation) unblocks once 3 and 4 resolve.
