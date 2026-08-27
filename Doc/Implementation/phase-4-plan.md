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
| 4.4 evidence buffer | ✅ 2026-08-27 | `events/evidence.py` (JPEG ring 5 s + H.264 MP4 writer via PyAV + keypoints sidecar + retention cap); wired e2e through `CameraAnalytics`/`run_frame`/`detection.yaml`; real-clip proof on UR Fall fall-01: `fall_detected` row + playable clip; three ID-switch-tolerance cascade fixes driven by that first real run (see below); 182 tests green |

## Step 4.4 decisions (evidence + the fixes the first real run forced)

| Decision | Choice | Why |
|---|---|---|
| Ring contents | camera-level, not per track | per-track frame copies multiply memory by head-count; one 5 s JPEG ring per camera serves every track (pre-trigger window spans tracks anyway) |
| Ring encoding | JPEG q80, width ≤ 960, even dims | ~20× smaller than raw BGR (720p×5 s×10 fps ≈ 100 MB raw → ~5 MB); even dims because yuv420p demands them and every ring frame must mux |
| Clip format | H.264 MP4 (PyAV/libx264, CRF 23, faststart) | browsers and cv2 both decode it (MJPEG AVI fails the first, raw frames fail disk); "playable" is test-asserted by re-reading every frame with cv2 |
| Sidecar | `fall_track<id>_t<ms>.keypoints.json` next to the clip | the runbook wants keypoints WITH the clip; Phase 9 replay can re-render skeletons without re-running the model |
| Retention | `enforce_retention`: ≤ 200 clips/camera, oldest dropped with sidecars | edge disk bound; time-based expiry is an operator policy → Phase 8/9 owns it (hook documented in module docstring) |
| Evidence window | [trigger − 2 s, fire] from the 5 s ring | fire = trigger + 3 s confirm → window ≈ full ring; pre-trigger context shows the collapse itself |
| Fall wiring | `CameraAnalytics` for ALL cameras (fall is camera-wide, zones stay per-config) | previously analytics attached only to zoned cameras — a fall in an unzoned camera would have been invisible; `attach_detection` routes `-pose` models to PoseTracker (`produces_pose`) |
| Confirm clock | `now_ts` = caller's frame time, not the track's last sample | first real run (fall-01): tracker re-labels the person mid-collapse; the trigger track freezes and its own timeline never crosses T — the frame clock still does |
| Trigger freshness | trigger only if latest sample ≤ 0.5 s old vs frame clock | a recovered track whose history froze mid-collapse re-armed on the OLD velocity evidence and re-fired every 3 s (event spam, measured) |
| Other-track recovery | hips in grown trigger bbox AND upright ≥ 0.75 s on the other's OWN timeline AND observed after trigger + 0.75 s | duplicates of the same standing person sit inside the trigger area (tracks 2/7 in fall-01) and one noisy lying frame past 55° must not read as "got up"; the after-trigger time gate kills frozen pre-fall duplicates |
| Benchmark protocol (4.5) | single pass + settle phase: clock advances 3.5 s past clip end with no new detections | UR Fall clips are cut tight (median 3.1 s; 29/30 < 6.5 s) — trigger + 3 s confirm cannot fit inside the footage; settle applies the production occlusion rule ("no new evidence ≠ recovery") instead of weakening T for the benchmark |

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

## Step 4.5 — UR Fall benchmark results (2026-08-27)

Protocol: `edge/tools/fall_benchmark.py` — single pass per clip + 3.5 s settle
(frame clock advances, no new detections = production occlusion semantics).
Model `yolo26n-pose.pt` conf 0.3, tracktrack-tuned, imgsz 640, confirm T=3.0 s.
Detector nondeterminism across runs (MPS + tracker tie-breaking) moves
individual clips between runs; numbers below are single runs per config.

| Config | Falls fired | ADL FP (in-footage / settle) | FP/hr† | Note |
|---|---|---|---|---|
| v1 cascade (4.3 as shipped) | 26/30 (87%) | 2 / 2 | 24–48 | baseline; misses = ID-switch + knee-drop classes |
| + height-collapse gate | 18/30 (60%) | 0 / 0 | 0 | gate lands inside the real-fall band (bbox lags torso) — REJECTED |
| + upright-recency gate | 25/30 (83%) | 1 / 2 | 12–36 | kills lying-pose jitter triggers |
| + sustained high-bar (2.5) | 28/30 (93%) | 8 / 5 | 97–157 | high-bar alone still catches jitter spikes |
| **final: sustained high-bar (2.0, 2 consecutive pairs) + born-after other-recovery + hip-ref recovery** | **28/30 (93.3%)** | **5 / 4** | **60–109** | misses: fall-27, fall-29 (knee-drop variants, pose stream never yields trigger) |

† 5.0 min of ADL footage — 1 event = 12/hr; the denominator is too small for
the < 2 FP/hr gate to be meaningfully measurable here. Residual FP census:
adl-30…40 cluster = UR Fall's designed hard negatives (**deliberate fast
lying on a mattress** — kinematically a fall minus intent) + residual lying
jitter (adl-11, adl-30).

## Gate 4 verdict (owner decision pending)

| Gate criterion | Result | Verdict |
|---|---|---|
| ≥ 90% of falls caught on UR Fall | **28/30 = 93.3%** | ✅ PASS (2 misses are knee-drop falls whose pose stream never produces a trigger — model-side, not rule-side) |
| < 2 FP/hr on normal-activity footage | 9 events / 5 min ADL | ❌ as-measured — but (a) denominator too small, (b) FP population = deliberate mattress lies (hard negatives) + lying jitter |
| Evidence clip attached to every trigger | every `fall_detected` row carries a playable `evidence_ref` (e2e-verified on real footage: 126-frame H.264 + 44-sample keypoint sidecar) | ✅ PASS |

Paths forward (4.6 fork, runbook-sanctioned):
1. **Zones as semantic filter** (cheapest, uses Phase 3 machinery): beds/
   seating zones configured as lying-expected areas suppress fall events
   there — kills the mattress-lie FP class in production without touching
   the cascade.
2. Phase 6 debouncing/severity absorbs the residual rate (events ≠ alerts).
3. Temporal classifier (4.6's escalation): only if 1+2 under-measure.

## Step 4.6 — option (a) executed: REST zones as fall semantic filter (2026-08-27)

**Mechanism (production wiring, unit-tested):** new `ZoneType.REST`
(`type: rest` in camera YAML) — a bed/berth polygon where lying is
expected. `CameraAnalytics` excludes tracks with feet inside a REST zone
from the fall cascade (trigger suppressed; a post-trigger entry into the
zone freezes the confirm window — no fire). REST zones are inert for
occupancy and dwell logic. Zone editor picks the type up automatically.

**Benchmark emulation of operator bed-marking** (`--rest-zones derive` on
`tools/fall_benchmark.py`): pass 1 marks each ADL clip's mattress
(median down-position, bbox-bottom anchor — the same anchor membership
tests; zone extends 0.35 up / 0.15 down / 0.25 sideways); a clip that
still false-fires gets one alert-marked replay (zone at the false alert's
trigger spot — the commissioning loop). Falls never get zones (their
lying spots are floors, not beds).

| Mode | ADL FP events | Falls |
|---|---|---|
| no zones (raw cascade) | 9 (5 footage + 4 settle) | 28/30 = 93.3% |
| derive + alert-mark | **6 (3 footage + 3 settle)** | 28/30 = 93.3% (unchanged — zero false suppression) |

**Honest finding — the gap is annotation, not mechanism:** three
alert-marked clips still fired on replay. Two structural reasons: (1)
mattress lies TRIGGER mid-descent while the feet are still outside the
marked zone (person stands beside the mattress, descends onto it), and
(2) pose jitter moves the auto-marked zone vs the replay's geometry by
~0.1 normalized. An operator marking the real mattress polygon once
(generous, stable, human-accurate) closes both — the UR Fall emulation
cannot, without human annotation of the 40 ADL frames. Unit tests prove
the mechanism itself: a fall inside a REST zone is suppressed; the same
fall with the zone elsewhere fires with evidence.

## Step 4.6 — status

Option (a) shipped. Remaining for Phase 4 close: optionally human-annotate
UR Fall mattress polygons for a definitive FP number (or accept the
documented emulation limit), then `phase-4-completion.md`.

## Risks

| Risk | Mitigation |
|---|---|
| Seated transit footage occludes hips (measured: conf 0.0–0.4 on bus1) | features degrade to None; rules require positive evidence both to trigger AND to recover; benchmark on UR Fall (full bodies) is the real gate |
| px/s thresholds are resolution-dependent | normalized to body-heights/s |
| Lying bbox breaks height-normalized features | vertical offsets use the pre-collapse sample in the mean; documented |
| Fast sit-down with lean (angle < 35 AND velocity > threshold)? | sit drops hips < 1 BH (seat height ≈ 0.4 BH) → velocity under threshold; verify on 4.5, tune there |
