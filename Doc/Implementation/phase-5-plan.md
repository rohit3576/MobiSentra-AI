# Phase 5 — Altercation Detection · Plan

> **Status: DRAFT — awaiting owner approval (2026-08-27). Execution is
> gated: Gate 3 (one verdict) + Gate 4 (FP fork) must close first
> (runbook rule: no next phase before the previous gate passes).**
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

## Steps

| Step | Plan | Done when |
|---|---|---|
| 5.1 runtime spike + model wiring | Download-script the weights (SOURCES.md entry: origin, no-license status, benchmark-only). Spike tflite-runtime vs TF vs ONNX on MPS with A2; pick one; `vision/action.py` wraps it behind a pure interface (same pattern as DetectorTracker: downstream never touches TF) | streaming scores over a sample clip; latency + FPS impact measured; runtime choice + numbers recorded here |
| 5.2 pair-finding | `analytics/pairs.py`: track pairs with overlapping/nearby boxes sustained > N frames → candidate interaction clips (crops around the union box feed the action model) | fighting pairs found before classification on test footage; unit tested with synthetic boxes |
| 5.3 signal fusion | `analytics/fight.py`: fight candidate = action score ≥ S AND proximity sustained AND rapid relative motion AND box-intersection oscillation (contact proxy). **Each signal alone must NOT trigger** — unit tested exactly that way | fusion unit tests: each signal alone silent, combined fires |
| 5.4 negatives corpus | `sample_data/negatives/` + SOURCES.md manifest: hugging, playing, rushing-to-exit, assisting, normal crowds — open-licensed real clips (Pexels/Commons, same hunt pattern as Gate-2 re-gate) | ≥ 30 min of negatives runnable as a regression suite for FP rate |
| 5.5 evaluation | Runner (extend `fall_benchmark.py` pattern): UBI-Fights test split (frame-level GT windows) + Hockey clips (2 s — trigger-stage only, settle-style confirm impossible; protocol documented) + the negatives set | numbers recorded: ≥ 85% detection, 0 alerts / 30 min negatives |

## Risks

| Risk | Mitigation |
|---|---|
| TF dependency inflates the edge image / MPS friction | tflite-runtime first; ONNX conversion as fallback; if both fail, A-series re-training is OUT of scope (post-MVP VideoMAE path is the locked later step) |
| engares repo unlicensed | zero code copied; our own wrapper; weights downloaded by script; noted in SOURCES.md + model zoo; optional license request to author |
| Dataset mirrors vanish (Hockey original already dead) | record mirror URLs + SHA256s in manifest at download time (UR Fall lesson) |
| 2-second Hockey clips vs confirm-window logic | UBI-Fights (variable length, frame GT) is primary; Hockey reported as trigger-stage metric only |
| Violence-classifier FP on crowded transit footage (the Wollongong 23%) | fusion (5.3) + negatives corpus (5.4) from day one — both are gate criteria, not afterthoughts |

## Open questions for the owner (answer with plan approval)

1. Kaggle API credentials for the two dataset mirrors — provide when 5.5
   starts (or accept Hockey-via-torrent / OpenDataLab manual fetch).
2. Runtime spike priority: accept my default (try tflite-runtime → TF →
   ONNX, decide on measured MPS latency)?
