# Phase 4 — Fall Detection · Plan

> **Status: RETROACTIVE DRAFT (written at Step 4.3, 2026-08-26).**
> Steps 4.1–4.2 executed on runbook detail per owner go before this doc
> existed — recorded here so 4.3+ decisions are reviewable. Owner review
> pending; execution of 4.3 proceeds on owner's explicit "start 4.3".
> Source of truth: `implementation-sequence.md` Phase 4 (on conflict, runbook
> + `implementation-plan.md` win).

## Objective

Fall/collapse events with low false positives: hybrid of keypoint rules
(v1) and — only if the gate demands it — a temporal classifier (deferred).

Model: **`yolo26n-pose.pt`** (YOLO26 family per the locked 2026-08-26
decision; `yolo11n-pose.pt` = one-line fallback). Wired in Step 4.1.

## Steps executed so far

| Step | State | Evidence |
|---|---|---|
| 4.1 pose swap | ✅ 2026-08-26 | `vision/pose.py` (TrackedPose, COCO-17 KeypointIndex, PoseTracker); keypoint buffer in TrackHistory; real-model smoke on bus1 (IDs persist, 17 kps); bus1 lesson: hips/nose often occluded → features must degrade to None |
| 4.2 features | ✅ 2026-08-26 | `analytics/fall_features.py` pure features (torso angle, head–hip distance + vertical offset, hip vertical velocity, bbox aspect); 16 synthetic-skeleton tests incl. occlusion degradation |
| 4.3 rule cascade | ✅ 2026-08-26 | `analytics/fall.py` FallDetector (trigger/confirm/recover state machine, BH/s-normalized velocity, positive-evidence recovery, schemas-v0 `fall_detected`); 10 sequence tests: fall vs sit vs bend, stumble-recovery, re-arm, occluded confirm, configurable T |

## Step 4.3 decisions (rule cascade v1)

| Decision | Choice | Why |
|---|---|---|
| Trigger (all required) | hip velocity ≥ 0.75 body-heights/s downward AND torso angle < 35° | hips sit ≈ mid-body → a collapse drops them ~0.5 BH in 0.3–0.6 s (0.8–1.7 BH/s); a slow sit ≈ 0.3–0.6 BH/s (seat height ≈ 0.3 BH); a bend leaves hips stationary (≈ 0). Velocity normalized by mean bbox height of the last two samples → resolution/distance independent |
| Head-near-hip | vertical offset \|head_y − hip_y\| / bbox height ≤ 0.25, optional (improves confidence, never blocks) | Euclidean head–hip distance normalized by bbox height is NOT discriminative when lying (bbox height collapses); the runbook's "head near hip level" is a vertical-offset statement. Occlusion (None) must not block a trigger seen on good core features |
| Recovery | positive evidence only: torso angle > 55° (margin above trigger) | 4.2 lesson — None features mean "unknown", not "fine": occlusion during the confirm window must not read as recovery |
| Confirm window | fires at first update with ts − trigger_ts ≥ 3.0 s (runbook T) still-down; configurable `confirm_seconds` | kills stumble-then-rise false positives |
| Re-arm | after firing, re-arms only on positive recovery evidence | no event spam from a person lying still |
| Track loss | `forget(track_id)` clears state; empty history auto-clears | no delayed fires on re-appearances |
| Confidence | 0.5 base + 0.25 if velocity ≥ 2× threshold + 0.25 if head-offset condition met, cap 1.0 | simple, monotone, honest about occlusion; Phase 6 owns severity |
| Event kind | `fall_detected` | schemas v0 `event_type` — Phase 6 envelopes without rename |
| Velocity normalization | mean bbox height of the last two samples | pre-collapse height dominates → conservative, resolution-independent |

## Remaining steps

| Step | Plan |
|---|---|
| 4.4 evidence buffer | 5 s ring per track; on trigger, snapshot clip + keypoints → `evidence_ref`; retention hook documented |
| 4.5 benchmark | UR Fall + Le2i download scripts (license recorded like Step 1.1); measure detection ≥ 90%, FP < 2/hr; hip-based anatomy finally testable on full-body footage |
| 4.6 tune | thresholds against 4.5 numbers; classifier ONLY if rules miss the gate |

## Risks

| Risk | Mitigation |
|---|---|
| Seated transit footage occludes hips (measured: conf 0.0–0.4 on bus1) | features degrade to None; rules require positive evidence both to trigger AND to recover; benchmark on UR Fall (full bodies) is the real gate |
| px/s thresholds are resolution-dependent | normalized to body-heights/s |
| Lying bbox breaks height-normalized features | vertical offsets use the pre-collapse sample in the mean; documented |
| Fast sit-down with lean (angle < 35 AND velocity > threshold)? | sit drops hips < 1 BH (seat height ≈ 0.4 BH) → velocity under threshold; verify on 4.5, tune there |
