# Phase 5 — Altercation Detection · Plan

> **Status: APPROVED 2026-08-28 (owner) — execution division below;
> steps run one-by-one per the per-phase working agreement.**
> Owner directive 2026-08-28: prior phase results reviewed and accepted as
> good; focus = project completion; proceed on documented defaults so
> execution never blocks. Gates 2/3/4 owner verdicts remain open tracker
> items (Phase-4 precedent: owner-sanctioned continuation, honestly
> recorded) — they do not block Phase 5 execution.
> Source of truth: `implementation-sequence.md` Phase 5 (on conflict, the
> runbook + `implementation-plan.md` §2 win).
> External facts below researched 2026-08-27 (librarian pass, URLs
> verified live that day).

## Objective

Fight/altercation events with the Wollongong lesson baked in from step
one: the field FP rate there was 23% — **negatives are the defense**, and
the action model NEVER alerts alone (fusion of model score + proximity +
rapid relative motion + contact proxy; severity mapping arrives in
Phase 6).

**Gate 5:** ≥ 85% fight-clip detection · 0 alerts on 30 min of normal
crowded interaction · negative regression suite committed.

## Research findings (2026-08-27, verified)

| Item | Facts | Consequence for us |
|---|---|---|
| `engares/MoViNets-for-Violence-Detection…` repo | **No LICENSE file** (GitHub API `license: null`) → all rights reserved. TF 2.15 codebase, SavedModel + TFLite, streaming mode. A0 76.7% / A1 80.2% / A2 81.2% / A3 80.4% test acc (5 fps, 12 fps for A3) | **We may not copy its code.** We write our own thin wrapper (`vision/action.py`) that loads the model file — interface use, not derivation. Weights come from HF `engares/MoViNet4Violence-Detection` (~1.8 GB) via download script, never committed. Optional courtesy: ask `engares` to add a license |
| Framework mismatch | Repo/model = TensorFlow (SavedModel / TF-Lite). Our edge stack = PyTorch/ultralytics on MPS | Step 5.1 starts with a **runtime spike**: `tflite-runtime` (light, no GPU training deps) vs full TF vs convert to ONNX. Decide on measured latency on MPS + a CPU-only sanity run; Jetson path favors TF-Lite/TensorRT later |
| Edge speed priors | TF blog: quantized A0 ≈ 200 fps, A1 ≈ 120 fps, A2 ≈ 60 fps on Pixel-4-class CPU | A0/A1 comfortably real-time; A3 unproven off x86. Default candidate: **A2** (best accuracy-per-cost with proven mobile runtime), A0 as fallback |
| Hockey Fights | Original host 404. Live mirrors: Academic Torrents + Kaggle (`yassershrief/hockey-fight-vidoes`). 1,000 clips (500/500), 720×576, 50 frames @25 fps, ~171 MB. License: none stated; paper says "available by request" | Kaggle-mirror download script (needs owner's Kaggle creds/API key — owner input), benchmark-only, never bundled, cite Nievas et al. CAIP 2011 |
| UBI-Fights | Official page unreachable (transport error). Live mirrors: Kaggle (`intissarziani/ubi-fightsall`), OpenDataLab (form-gated). 1,000 videos (216 fight / 784 non-fight), 640×360 @30 fps, **frame-level labels**, ~80 h | Same posture. Frame-level GT makes it the PRIMARY gate dataset (Hockey clips are only 2 s — too short for our confirm-window style logic, as UR Fall taught us) |
| RWF-2000 | Non-commercial | Banned, as locked (implementation-plan §0) |

## Steps (summary)

| Step | Plan | Done when |
|---|---|---|
| 5.1 runtime spike + model wiring | Download-script the weights (SOURCES.md entry: origin, no-license status, benchmark-only). Spike tflite-runtime vs TF vs ONNX on MPS with A2; pick one; `vision/action.py` wraps it behind a pure interface (same pattern as DetectorTracker: downstream never touches TF) | streaming scores over a sample clip; latency + FPS impact measured; runtime choice + numbers recorded here |
| 5.2 pair-finding | `analytics/pairs.py`: track pairs with overlapping/nearby boxes sustained > N frames → candidate interaction clips (crops around the union box feed the action model) | fighting pairs found before classification on test footage; unit tested with synthetic boxes |
| 5.3 signal fusion | `analytics/fight.py`: fight candidate = action score ≥ S AND proximity sustained AND rapid relative motion AND box-intersection oscillation (contact proxy). **Each signal alone must NOT trigger** — unit tested exactly that way | fusion unit tests: each signal alone silent, combined fires |
| 5.4 negatives corpus | `sample_data/negatives/` + SOURCES.md manifest: hugging, playing, rushing-to-exit, assisting, normal crowds — open-licensed real clips (Pexels/Commons, same hunt pattern as Gate-2 re-gate) | ≥ 30 min of negatives runnable as a regression suite for FP rate |
| 5.5 evaluation | Runner (extend `fall_benchmark.py` pattern): UBI-Fights test split (frame-level GT windows) + Hockey clips (2 s — trigger-stage only, settle-style confirm impossible; protocol documented) + the negatives set | numbers recorded: ≥ 85% detection, 0 alerts / 30 min negatives |

## Execution division (approved 2026-08-28 — "step by step so we do not get blocked")

**Design rule:** every sub-step is independently executable and verifiable
(one-by-one per the working agreement — no batching ahead, no skipping).
Exactly **one** sub-step needs owner input (5.5b, Kaggle creds); everything
else runs autonomously on the defaults locked below. Sub-steps use the
Phase-4 letter convention (cf. 4.6a).

**Dependency / blocking map:**

```
5.1a → 5.1b → 5.1c      (model path; needs no owner input — weights are on HF)
5.2                      (pure logic — synthetic tests only, blocks nothing)
5.3   (consumes 5.2 outputs; developed against synthetic inputs)
5.4                      (clip hunt — independent of 5.1–5.3, needs no creds)
5.5a (runner) → 5.5b (OWNER: Kaggle creds) → 5.5c (run + Gate 5 verdict)
```

If 5.1b hits TF-on-MPS friction: fall through the spike ladder
(tflite-runtime → TF → ONNX) and record why — **retraining A-series is out
of scope** (post-MVP VideoMAE is the locked later path). If 5.5b creds are
unavailable: documented fallbacks (Academic Torrents for Hockey, OpenDataLab
manual fetch for UBI-Fights) — 5.5a still builds against stub/synthetic GT.

### 5.1 — Action model wiring (no owner input)

| Sub | Do | Files | Done when |
|---|---|---|---|
| 5.1a weights + manifest ✅ 2026-08-28 | download script for HF `engares/MoViNet4Violence-Detection` (A2 default, A0 fallback), SHA256 + size recorded; SOURCES.md row: origin, unlicensed-repo status, benchmark-only, wrapper-only posture; gitignored like UR Fall | `mlops/datasets/download_movinet.py`, `mlops/datasets/SOURCES.md` | ✅ A2 triplet on disk (35.26 MB) + manifest.csv; SOURCES.md row added; git clean of weights |
| 5.1b runtime spike ✅ 2026-08-28 | measure the ladder on MPS with A2 over a 20–30 s sample stream: per-frame latency + end-to-end FPS impact; CPU-only sanity run; record numbers in this doc | results table → this doc | ✅ **runtime picked: ONNX explicit-states (onnxruntime), 10.6 ms/frame, semantics verified, numerically exact vs TF** — full ladder in Step 5.1 decisions; harness `edge/tools/spike_movinet_runtime.py`, results `edge/runs/movinet-spike.json` |
| 5.1c wrapper ✅ 2026-08-28 | `ActionScorer` behind a pure interface (DetectorTracker pattern — downstream code never imports TF); onnxruntime engine; streaming state carry (73 tensors); warm-up semantics documented; unit tests run against a recorded-score stub (no runtime import in the test path) | `edge/mobisentra/vision/action.py` + tests | ✅ 10/10 stubbed tests green (state carry, reset, letterbox geometry, RGB/scale, int32 state dtype, warmup, output-count validation); real-clip proof: bus1.mp4 96 steps P(Fight) 0.34 cold → **0.010 settled** (evidence warm-up), UR Fall adl-01 max 0.338/end 0.156 (no fight); latency via our interface **11.4 ms mean / 14.0 ms p95** (matches spike 10.3); artifact = `mlops/datasets/movinet/movinet_a2_explicit_states.onnx` (24.9 MB, gitignored) + provenance sidecar; exporter `tools/export_movinet_onnx.py` (spike-venv, offline) |

### 5.2 — Pair-finding (pure logic; no model, no dataset)

- **Do:** `analytics/pairs.py` — track pairs with IoU/center-distance
  proximity sustained > N frames → candidate interaction pairs; expose the
  union box (crop source for the action model).
- **Done when:** unit tests with synthetic boxes (sustained overlap fires;
  single-frame overlap doesn't; distant pairs never pair); on sample
  footage, two interacting people become a candidate pair before any
  classification runs.

### 5.3 — Signal fusion (pure logic; consumes 5.2)

- **Do:** `analytics/fight.py` — candidate = action score ≥ S AND proximity
  sustained AND rapid relative motion AND box-intersection oscillation
  (contact proxy). Wire beside fall in `CameraAnalytics`; emit schemas-v0
  candidate events (event kind verified against the locked v0 type list at
  execution; `fall_detected` naming precedent). Clean re-arm semantics;
  severity + debouncing stay Phase 6.
- **Done when:** fusion unit tests prove each signal alone is silent and
  only the combination fires; synthetic sequence suite (fight vs hug vs
  pass-by vs rush) green.

### 5.4 — Negatives corpus (no owner input; no creds)

- **Do:** hunt + download ≥ 30 min open-licensed normal-interaction clips
  (hugging, playing, rushing-to-exit, assisting, normal crowds) —
  Pexels/Pixabay/Commons, same hunt pattern as the Gate-2 re-gate; manifest
  with origin + license per clip.
- **Files:** `edge/sample_data/negatives/` + manifest (SOURCES.md pattern).
- **Done when:** ≥ 30 min footage on disk, manifest complete, one command
  runs the whole set as the FP regression input.

### 5.5 — Evaluation (⚠️ 5.5b is the ONLY owner-input step)

| Sub | Do | Done when |
|---|---|---|
| 5.5a runner | `tools/fight_benchmark.py` (extends the fall_benchmark pattern): UBI-Fights frame-level GT windows = primary; Hockey = trigger-stage-only (2 s clips, protocol documented); negatives soak = FP rate | runner executes end-to-end on stub/synthetic inputs; metrics emitted as JSON |
| 5.5b datasets | **OWNER: Kaggle API creds** (or pick a fallback: Academic Torrents / OpenDataLab manual fetch). Download scripts + SHA256s, gitignored, SOURCES.md rows (Nievas et al. CAIP 2011 citation for Hockey) | both datasets verifiable on disk; no dataset file tracked by git |
| 5.5c run + verdict | full run: detection rate on UBI-Fights fight windows + Hockey trigger-stage; FP = alerts over the 30-min negatives set; fill the Gate 5 table | numbers recorded here + Gate 5 verdict row (PASS/FAIL per criterion, evidence links) |

## Step 5.1 decisions (to fill at execution)

| Decision | Choice | Why |
|---|---|---|
| **5.1a weights variant (2026-08-28)** | A2 = `movinet_a2_5fps_32bs_0.001lr_0.3dr_0tl` (acc 0.8122, F1_fight 0.8184, **recall_fight 0.8466**); A0 fallback = `movinet_a0_5fps_32bs_0.001lr_0.2dr_2tl` | picked from engares `model_performance_metrics.csv` (best per architecture); recall weighted for the Gate-5 miss criterion; both 5 fps-input models. Their README's own best is A3-12fps (0.85) — rejected per §research (12 fps input cost, unproven off x86) |
| **5.1a format fact (2026-08-28)** | HF ships **TF checkpoints only** (35.26 MB triplet; no .tflite / no SavedModel on the hub). Whole repo = 1.97 GB / ~60 variants — we fetch one variant | 5.1b consequence: the "tflite-runtime first" rung requires a **convert-from-checkpoint** step (their export Colab) or full-TF load; ladder order unchanged, extra hop recorded. Weights + SHA256s on disk (`movinet/manifest.csv`), gitignored, SOURCES.md row added (RWF-2000 training tag → non-commercial-derived, benchmark-only) |
| runtime (tflite / TF / ONNX) | **ONNX with explicit states (onnxruntime)** — decided 2026-08-28, measured (spike round 3, CPU M-series, 96 frames @5fps) | **10.6 ms/frame, streaming semantics verified (order-permutation check), logits numerically identical to TF (match to 1e-4)**; 24.9 MB, 73 state tensors fed back per call. Ladder evidence: TF eager 256 ms (Keras overhead) / **TF graph 14.9 ms (viable fallback)**; TFLite **dead on macOS** — `DepthwiseConv2dNative` won't lower builtin-only on TF 2.20 and flex ops need a delegate the Python interpreter lacks (Jetson/Linux path unaffected); ONNX naive stateless 9.5 ms but semantics broken (states baked). Budget: 5 fps scorer = 200 ms/frame → **~19× headroom**, preprocess 0.21 ms. Edge env gets `onnxruntime` only (pip-light, PyTorch-stack affinity); TF is offline-conversion-only. Export recipe = `attempt_onnx_states` in `edge/tools/spike_movinet_runtime.py`; results: `edge/runs/movinet-spike.json` |
| model variant (A2 / A0 fallback) | A2 confirmed on the winner runtime (decided 5.1a) | weights load 649/649 vars through our own Google-code wrapper; sanity P(No_Fight)=0.99 on normal bus footage |

## Risks

| Risk | Mitigation |
|---|---|
| TF dependency inflates the edge image / MPS friction | tflite-runtime first; ONNX conversion as fallback; if both fail, A-series re-training is OUT of scope (post-MVP VideoMAE path is the locked later step) |
| engares repo unlicensed | zero code copied; our own wrapper; weights downloaded by script; noted in SOURCES.md + model zoo; optional license request to author |
| Dataset mirrors vanish (Hockey original already dead) | record mirror URLs + SHA256s in manifest at download time (UR Fall lesson) |
| 2-second Hockey clips vs confirm-window logic | UBI-Fights (variable length, frame GT) is primary; Hockey reported as trigger-stage metric only |
| Violence-classifier FP on crowded transit footage (the Wollongong 23%) | fusion (5.3) + negatives corpus (5.4) from day one — both are gate criteria, not afterthoughts |

## Open questions — resolved at approval (2026-08-28)

1. **Kaggle API credentials** → deferred to **Step 5.5b**, the only
   owner-input step in the phase. Nothing before it blocks (5.1a weights
   come from HuggingFace; 5.4 negatives from Pexels/Commons). Fallbacks if
   creds are unavailable: Academic Torrents (Hockey), OpenDataLab manual
   fetch (UBI-Fights).
2. **Runtime spike priority** → owner accepted the default via the
   2026-08-28 "don't get blocked" directive: tflite-runtime → TF → ONNX,
   decided on measured MPS latency (5.1b records the numbers either way).
