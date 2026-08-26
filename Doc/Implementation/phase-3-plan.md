# Phase 3 — Zones, Occupancy, Door Rules · Plan

> **Status: DRAFT — awaiting owner approval before implementation**
> Source of truth: [`implementation-sequence.md`](./implementation-sequence.md) → Phase 3 (on conflict, runbook + `implementation-plan.md` win)
> Duration: ~1 week · Budget: 4 build days + 1 validation/doc day + 2 buffer
> Prerequisite: **Gate 2 owner decision** (phase-2-completion.md §8) — plan may be reviewed in parallel; execution must not start before the decision

---

## 1. Objective

First real safety events from pure geometry + rules — no ML in this phase:

> `TrackedPerson[] per frame → zone membership (feet point) → occupancy bands / dwell events → candidate-event JSONL`

Everything downstream of Phase 2's tracker. Output is **candidate events** (internal JSONL); Phase 6 turns them into CloudEvents + severity, Phase 7 ships them over MQTT.

## 2. Gate 3 (from runbook, made measurable)

| # | Criterion | Measured by |
|---|---|---|
| 1 | Occupancy within ±10% of manual count | `tools/occupancy_check.py` on `crowd_real_01.mp4`: owner counts people in the platform zone at 5 sampled frames; tool prints measured distinct-track counts at exactly those frames; per-frame diff ≤ 10% (or ±1 person when count < 10, absolute) |
| 2 | Zero false zone events over 30 min empty-zone footage | 30-min loop of bundled synthetic clips (no persons → no detections) with zones configured: **0** zone events in `runs/events/`; supplemented by real-clip run where every emitted intrusion event maps to a visually confirmable loiterer (spot-check in review pack) |
| 3 | Zone editor round-trips YAML | polygon drawn with `tools/zone_editor.py` → exported snippet → loaded by registry parser → engine membership matches the drawn region (unit test with the exported fixture) |

## 3. Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Zone membership | `supervision.PolygonZone`, **BOTTOM_CENTER anchor (feet)**, polygons denormalized from registry `[0..1]` coords using frame size captured on first analyzed frame | runbook-locked; feet define physical presence in a zone; warn (not crash) if frame size changes |
| Registry typing | Extend `ZoneConfig` with explicit `type: occupancy \| restricted \| door` + `max_capacity: int \| None` (occupancy only); update `cameras.yaml` sample + parser | implicit name-based typing is fragile; explicit survives renames; registry already carries the data — parser just reads it now |
| Occupancy count | distinct `track_id`s with feet inside an occupancy zone in the current analyzed frame | per-frame simultaneous presence is what capacity means |
| Occupancy bands | ratio = count / max_capacity → Normal < 0.70 · Moderate 0.70–0.90 · Crowded > 0.90 · Overcrowded > 1.00 | runbook values |
| Hysteresis | band change confirmed only after `occupancy_confirm_frames` **consecutive analyzed frames** (registry value, default 30) | kills boundary flicker; analyzed-frame basis matches `analyze_every_n_frames` throttling |
| Dwell semantics (Phase 2 lesson) | in-zone time accumulates per (track_id, zone) from wall-clock `capture_ts`; detection dropouts ≤ 0.5 s tolerated (not reset); event at accumulated ≥ threshold; re-arm only after the track leaves ≥ 2× threshold or is purged | ID/detection dropouts at 0.741 fragmentation must not reset legitimately loitering tracks; wall-clock not frame-count (framerate varies) |
| Event sink | JSONL to `runs/events/<camera_id>.jsonl` (gitignored): `occupancy_level_change`, `restricted_zone_entry`, `door_obstruction` — kind strings are schemas v0 `event_type` values so Phase 6 envelopes candidates without renaming; payload = camera, zone, track_id, band/count/ratio, first_seen_ts, ts | Phase 6 owns envelopes/severity/MQTT — Phase 3 vocabulary matches schemas v0 event kinds |
| Door telemetry slot | event payload reserves `door_state: "unknown"` field; **no** MQTT input implementation | runbook Step 3.4 reservation |
| Wiring | analytics auto-on when a camera has zones and `--detect` is active (one log line); no new CLI flags; zone ops run on every analyzed frame | zero-config for the sample registry; flags breed drift (Phase 2's `--track-buffer` lesson) |
| Zone editor | minimal OpenCV window: click points, `r` reset, Enter closes polygon → prints/writes normalized YAML `zones:` snippet | runbook "simple is fine"; polish is a labeled `good first issue` |
| FPS impact | membership = point-in-polygon per person per zone per analyzed frame — negligible; re-run `tools/bench.py --seconds 30` once on completion day as evidence | keep the ≥ 15 FPS gate honest after every phase |

## 4. Module Plan

```
edge/mobisentra/analytics/
├── __init__.py
├── zones.py           # ZoneEngine: per-camera PolygonZone set; (ts, frame, people)
│                      #   → {zone_name: set(track_id)}; pure wrapper, supervision hard dep
├── occupancy.py       # OccupancyMonitor: counts → ratio → band + N-frame hysteresis
│                      #   → occupancy.band_change events (pure logic, no cv)
└── zone_events.py     # DwellTracker: (track, zone) accumulation w/ dropout tolerance,
                       #   restricted-loiter + door-obstruct candidate events (pure)
edge/mobisentra/ingestion/config.py   # ZoneConfig: + type, + max_capacity (parser + tests)
edge/mobisentra/main.py               # wire analytics into run_frame; events JSONL sink
edge/tools/zone_editor.py             # click-polygon → normalized YAML snippet
edge/tools/occupancy_check.py         # gate evidence: prints measured counts at sampled frames,
                                      #   compares against owner-entered manual counts
edge/tests/
├── test_zone_engine.py      # synthetic boxes/polygons: inside/outside/edge, feet anchor,
│                            #   frame-size denormalization, mask↔track_id zipping
├── test_occupancy.py        # band boundaries, no flicker at boundary (hysteresis),
│                            #   confirm-frame counting on analyze-every-n sequences
├── test_zone_events.py      # dwell accumulate, ≤0.5s dropout tolerated, >0.5s reset,
│                            #   re-arm cooldown, threshold crossing fires exactly once
└── test_zone_config.py      # registry: typed zones parse, max_capacity validation,
                             #   zone-editor exported YAML round-trips
```

No new dependencies — `supervision` added in Phase 2, `opencv` already in.

## 5. Test & Validation Data

| Need | Source | Bundled? |
|---|---|---|
| Unit tests (pure logic) | synthetic boxes + fake clocks — no video needed | ✅ |
| Occupancy vs manual count (gate 1) | `crowd_real_01.mp4` real platform crowd; platform zone = full-frame band | ✅ |
| Empty-zone 30-min FP soak (gate 2) | bundled synthetic clips looped 30 min (zero detections expected — pipeline sanity) + real-clip intrusion spot-check pack | ✅ |
| Zone editor round-trip (gate 3) | saved frame from any bundled clip | ✅ |

Manual counting is **owner time (~10 min)**: 5 frames, count heads in zone. `occupancy_check.py` prints the frame files + measured numbers; owner fills a `manual=` dict; script computes the ±10% verdict table.

## 6. Gate Metric Details

- **Occupancy accuracy:** per sampled frame, `|measured − manual| ≤ max(1, 0.10 × manual)`. All 5 frames must pass. Measured = distinct track IDs with feet in zone at that frame (from `--debug-detections` JSONL replay or live re-run — deterministic either way).
- **False-positive soak:** 30 min synthetic loop, all 3 sample cameras with zones active → events file must contain **zero** rows. Real-clip supplementary: every `zone.intrusion` row pairs with a review-pack frame (reuse Phase 2's dump tool with zone overlay added on completion day).
- **Round-trip:** exported YAML snippet → `load_camera_registry` → `ZoneEngine` membership on synthetic points matches the clicked polygon within editor visualization tolerance (exact, coordinates are copied).

## 7. Schedule — one step per day, verifiable before the next (runbook order)

| Day | Runbook step | Deliverable (done-when) | ☐ |
|---|---|---|---|
| 1 | 3.1 | `ZoneConfig` typed + registry sample updated; `zones.py` ZoneEngine; `test_zone_engine.py` + `test_zone_config.py` green | ☐ |
| 2 | 3.2 | `occupancy.py` + `test_occupancy.py` green — hysteresis proven (boundary sequence: no flicker) | ✅ 2026-08-26 |
| 3 | 3.3 + 3.4 | `zone_events.py` DwellTracker (restricted + door share the mechanic) + `test_zone_events.py` green — dropout tolerance + re-arm proven | ✅ 2026-08-26 |
| 4 | wiring | `main.py` runs analytics when zones present; `runs/events/*.jsonl` produced on real clip; `--preview` shows zone overlays; ruff + full suite green | ☐ |
| 5 | 3.5 | `tools/zone_editor.py` + round-trip unit test green (gate 3 evidence) | ☐ |
| 6 | 3.6 | `tools/occupancy_check.py`; owner manual counts (5 frames); 30-min synthetic FP soak; real-clip intrusion spot-check | ☐ |
| 7 | buffer | `phase-3-completion.md`; bench sanity (≥ 15 FPS still); gate review with owner | ☐ |

Rule: a day closes only with its tests/evidence green — no batching ahead (per-phase working agreement).

## 8. Risks

| Risk | Mitigation |
|---|---|
| ID fragmentation (Gate 2 carry-over, 0.741) inflates occupancy counts | per-frame distinct-ID counting: walk-through re-IDs rarely coexist in-zone (old ID gone before new spawns); worst case +1 transient — inside the ±10%/±1 gate tolerance; documented in completion |
| Fragmented IDs break dwell accumulation mid-loiter | ≤ 0.5 s dropout tolerance + wall-clock accumulation (design §3); longer losses re-fire as new events (acceptable, counted honestly) |
| Frame size ≠ first-frame size (RTSP resolution switch) | warn + re-denormalize polygons on detected change (rare; sample sources fixed) |
| Synthetic soak is vacuous (no detections at all) | kept as pipeline sanity per runbook, supplemented by real-clip spot-check where events must match visible loitering |
| Manual count subjective at crowd density | 5 low-ambiguity frames chosen by the tool; ±1 absolute tolerance under 10 people |
| PolygonZone anchor surprise (feet vs center) | explicit unit test: person box overlapping zone edge counts only when feet are inside — matches physical intuition |

## 9. Out of Scope (explicit)

- Event severity, CloudEvents envelopes, MQTT publishing (Phase 6/7)
- Seat-level occupancy, line-crossing, abandoned objects (post-MVP per plan §1)
- Door telemetry integration (slot reserved only)
- Zone editor GUI polish (good-first-issue after v0.1.0)
- Per-zone threshold overrides (global per-camera thresholds only, as registry ships today)

## 10. Approval

- [ ] Owner reviews this plan (typed registry zones, feet-anchor membership, dwell dropout tolerance, owner manual-count task)
- [ ] Owner records Gate 2 decision (phase-2-completion.md §8) — execution may start the next working day after both boxes are ticked
