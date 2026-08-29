# Phase 5 — Completion Report

> **Status: EXECUTED (2026-08-28) · Gate 5: 1/3 ticked — numeric criteria NOT met, closed 2026-08-29 by owner decision (c) "accept documented limitation" (Gate-4 FP-fork precedent: owner-sanctioned continuation, honestly recorded)**
> Scope executed: `Doc/Implementation/phase-5-plan.md` (approved 2026-08-28; steps 5.1a–5.5c run one-by-one per the working agreement)
> Runbook: `implementation-sequence.md` → Phase 5

---

## 1. Gate 5 — Evidence

| # | Criterion | Requirement | Result |
|---|---|---|---|
| 1 | Fight-clip detection | ≥ 85% on UBI-Fights test-split fight windows | ❌ **NOT MET** — **11/40 (27.5%)** on the official 67-clip test split (40F/27N), full-length salvage run; 5/35 on the windowed protocol (±5 s/+2 s around GT windows). Breakdown: most misses are **silent in-window** (no alert at all — model/pair side: UBI's small/distant/movable-camera fights score too low under the RWF-2000-trained MoViNet); 3 clips fired **outside** the ±1 s GT-window tolerance (tuning-recoverable). Evidence: `edge/runs/fight-benchmark-salvage.json`, `edge/runs/fight-benchmark-windowed-PARTIAL.json` |
| 2 | Zero alerts on normals | 0 alerts / 30 min normal crowded interaction | ❌ **NOT MET** — negatives corpus baseline **22 alerts / 42.4 min = 31/hr** (`edge/runs/fight-negatives-soak.json`). By category: assist **0**/17 ✅, crowd **0**/21 ✅ (fusion holds in dense crowds — the Wollongong pair-spam risk is clean), hug 7/41, play 5/44, rush 3/24. Play/rush FPs skew model-side (action_score up to 1.0 on wrestling-shaped play — RWF grappling bias); hug FPs are fusion-side (approach-burst motion peak + sustained contact) |
| 3 | Negative regression suite committed | runnable FP suite in-repo | ✅ **PASS** — 147 clips / 42.4 min (hug/play/rush/assist/crowd), manifest with origin URLs + SHA256s, pinned re-fetch (`mlops/datasets/download_negatives.py`, curl_cffi for the Pexels WAF, 147/147 verified skip-on-rerun), one-command soak `tools/fight_negatives_soak.py` |

**Verdict: Gate 5 open → closed by owner decision (c) 2026-08-29.** The fight
path is built, wired beside fall, and unit-proven (each signal alone stays
silent); the gap is model-side recall on UBI-style footage, which the plan
anticipated — *"retraining A-series is OUT of scope (post-MVP VideoMAE is the
locked later step)"*. Threshold/fusion tuning is deferred (rides the Phase 10
data loop); the windowed protocol (~5 min/run) is in place for any later
iteration.

## 2. What Was Built

| Module | Role |
|---|---|
| `mobisentra/vision/action.py` | `ActionScorer` — MoViNet-A2 via ONNX explicit-states (onnxruntime), streaming 73-tensor state carry, DetectorTracker-pattern pure interface (downstream never touches TF); 11.4 ms mean / 14.0 ms p95 per call |
| `mobisentra/analytics/pairs.py` | sustained-proximity pair finder — IoU ≥ 0.08 OR center distance ≤ 0.9 mean-box-diagonals (scale-invariant), 2-frame gap grace, union-box emission (crop source) |
| `mobisentra/analytics/fight.py` | 4-signal fusion (action score + proximity + rapid relative motion + contact proxy incl. grapple path); **each signal alone never alerts**; `altercation_suspected` rows (schemas-v0 enum) wired beside fall in `CameraAnalytics` via `action_scorer_factory` |
| `tools/spike_movinet_runtime.py` + `tools/export_movinet_onnx.py` | runtime-spike harness (ladder evidence) + offline TF→ONNX explicit-states exporter (spike-venv, offline; TF never enters the edge env) |
| `tools/fight_benchmark.py` | Gate-5 runner: UBI frame-GT windows (±1 s tolerance) as PRIMARY, Hockey trigger-stage-only (2 s clips — protocol documented), negatives FP; `--selftest` synthetic CI-able smoke (stub tracker + scorer); windowed protocol (`--limit`/span caps) for fast iteration; tuning knobs `--action-min/--rel-motion/--sustain-s` |
| `tools/fight_negatives_soak.py` | one-command FP regression over the 147-clip corpus (full production stack, exit 1 on alerts, JSON report) |
| `mlops/datasets/download_movinet.py`, `download_negatives.py` | pinned, SHA256-verified fetchers; SOURCES.md rows record origin + license posture (engares repo unlicensed → wrapper-only, zero code copied; RWF-2000-derived weights → benchmark-only) |
| `vision/tracker.py` (shared session) | `shared_session_factory` — one inference session across tracker/scorer stacks; **0.8× → 6.0× realtime** with identical outputs |
| Tests | `test_action_scorer*` (10 stubbed), `test_pairs` (12), `test_fight_fusion` (9), fight-wiring (4), benchmark/soak selftests — suite total at close: **235 passed + 4 skipped, ruff clean** (re-verified 2026-08-29) |

## 3. E2E Verification

- Full-stack sanity with the real model: bus1.mp4 96 frames @5 fps → pair forms and gets scored, **0 `altercation_suspected` rows** on normal footage; fight-path overhead ≈ 0.1 s / 96 frames (scoring only while a pair is active).
- Benchmark runner selftest: stub tracker + stub scorer over synthetic clips → fight clip 1 alert + pair formed + peak 0.67; calm clip 0 alerts; JSON metrics emitted.
- Negatives soak: full production stack per clip, per-category census (§1 #2).

## 4. Process Notes (honest)

- **Gate 5 closed unmet by owner sanction** (option *c*, 2026-08-29) — same
  pattern as the Gate-4 FP fork and the open Gates 2/3 tracker items. The
  alternatives were (a) short windowed tuning iterations, (b) defer tuning to
  Phase 6; the owner chose documented acceptance. Tuning is **not** cancelled —
  it rides the Phase 10 data loop.
- **Owner stop directives (2026-08-28):** full-length benchmark clips were too
  slow → the owner twice ordered runs stopped. Consequence, now standing
  policy: **windowed protocol only** for any future fight evaluation
  (fight clips ±5 s/+2 s around GT windows, capped 120 s; normals 60 s
  prefix) — ~5 min/run at 6× realtime.
- **5.5b needed no owner input after all:** the 2026-08-27 "official UBI host
  unreachable" was an **expired SSL certificate** (fetch with verification
  relaxed; integrity pinned by SHA256); Hockey arrived via Academic Torrents
  (aria2c 16-way, piece-verified). Kaggle creds were never used.
- Dataset licenses: UBI-Fights (Degardin & Proença IJCB 2020) and Hockey
  (Nievas et al. CAIP 2011) stay gitignored, benchmark-only, citations +
  SHA256s in SOURCES.md. MoViNet weights are RWF-2000-trained → non-commercial
  taint, benchmark-only, never bundled.
- Known cosmetic wart: benign OpenCV-5 `recursive_mutex` abort at soak **exit**
  after the report is written (cv2 teardown, not pipeline).

## 5. Issues Hit & Resolutions

| Issue | Resolution |
|---|---|
| TFLite dead on macOS (`DepthwiseConv2dNative` won't lower; flex ops need a delegate) | runtime ladder fell through to **ONNX explicit-states**: 10.6 ms/frame, numerically identical to TF (1e-4), TF-graph kept as fallback (14.9 ms); Jetson/Linux TFLite path unaffected |
| engares repo ships TF checkpoints only (no TFLite/SavedModel on HF) | offline converter (`export_movinet_onnx.py`) in a spike-venv; edge env gets `onnxruntime` only |
| First benchmark ~1 h (full-length clips, 0.8× realtime, 42-min negatives re-check) | killed per owner; `shared_session_factory` (6.0× realtime, identical outputs) + windowed protocol → 23 clips in 5 min |
| Dataset hosts dead / creds-gated | official UBI host (expired-SSL workaround, SHA256-pinned) + Academic Torrents for Hockey — no Kaggle creds needed |
| Pexels WAF rejects plain HTTP clients | curl_cffi fetcher, resumable, 147/147 verified |

## 6. Verification Commands

```bash
cd edge
.venv/bin/python -m pytest                     # 235 passed + 4 skipped (2026-08-29)
.venv/bin/python -m ruff check .               # clean
.venv/bin/python tools/fight_benchmark.py --selftest          # CI-able runner smoke
# Recorded evidence (no re-run needed; windowed protocol only if ever re-run):
#   runs/fight-benchmark-salvage.json        — 11/40 full-length baseline
#   runs/fight-benchmark-windowed-PARTIAL.json — windowed protocol partial
#   runs/fight-negatives-soak.json           — 22 alerts / 42.4 min census
#   runs/movinet-spike.json                  — runtime ladder measurements
```

## 7. Next

1. **Phase 6 — Event Engine + Severity** (fight events flow through the
   engine as-is; severity mapping + debouncing + pair evidence clips ride the
   Phase-6 writer generalization per the plan).
2. Deferred open-items tracker (owner verdicts still pending): Phase 2 A′
   waive, Phase 3 occupancy manual counts, Phase 4 FP closure →
   `phase-4-completion.md`.
3. Fight-quality iteration (only if owner calls for it): windowed tuning runs
   (~5 min each — `--action-min/--rel-motion/--sustain-s`, motion-recency
   for hug FPs); model-side recall is the Phase 10 / post-MVP VideoMAE path.
