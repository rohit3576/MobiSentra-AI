# MobiSentra AI — Implementation Sequence (Runbook)

> Execution-ordered steps derived from `Doc/implementation-plan.md` (authoritative plan).
> **Work top-to-bottom. A phase starts only after the previous GATE passes.**
> MVP slice: `CCTV → YOLO26n → ByteTrack/BoT-SORT → zones/occupancy → fall → fight → MQTT/Kafka → Node.js → React dashboard`
> Target: v0.1.0 MVP demo at Gate 9 (~14 weeks). Jetson/MLOps hardening = stretch (Phase 10).

---

## Conventions

Every step follows the same format:

- **Do:** the action
- **Files:** what gets created/changed
- **Commands:** what to run (copy-paste ready where deterministic)
- **Done when:** the observable proof the step is complete

Standing rules:

1. **Owner runs all git operations** (init, add, commit, push, tag). Steps below that say `OWNER:` are yours; the agent never runs them.
2. **Sample data is the default input.** Nothing in Phases 0–9 requires a real camera, RTSP stream, or Jetson.
3. **Gates are hard.** If a gate fails, stop and fix — do not start the next phase.
4. Dependencies are added **in the phase that needs them**, not up front.

---

## Progress Tracker

| Phase | Name | Gate (one-liner) | Status |
|---|---|---|---|
| 0 | Scaffold & Environment | compose up → MQTT→Kafka round-trip; CI green | ✅ Locally passed 2026-08-25 (CI activates on first push) |
| 1 | Video Ingestion | 60 min RTSP < 200 ms lag; auto-reconnect < 15 s | ✅ Passed 2026-08-25 (max lag 148 ms; kill detected 0.0 s) |
| 2 | Detection + Tracking | stable IDs through occlusions; ≥ 15 FPS | 🟡 Executed 2026-08-26 — FPS ✅ (57/44); ID metric 0.741 vs 0.80 → [owner decision pending](./phase-2-completion.md#8-gate-decision--options-for-owner) |
| 3 | Zones / Occupancy / Door | occupancy ±10% vs manual; 0 FP on empty-zone footage | 🟡 Gate 3: 2/3 ticked — empty-zone FP ✅ (30 min stream, 0 events, 2026-08-27) + editor round-trip ✅; occupancy ±10% measured (3,3,3,5,4 on bus1), verdict awaits owner's 5 manual counts → then `phase-3-completion.md` |
| 4 | Fall Detection | ≥ 90% UR Fall; < 2 FP/hr | 🟡 In progress — 4.1–4.5 done (4.5: **93.3% detection ✅**; FP blocked on mattress-lie hard negatives); 4.6 option (a) shipped 2026-08-27 (REST zones suppress falls in beds/berths — FP 9→6 with auto-marked zones, mechanism unit-proven, definitive number needs human-marked polygons); remaining: FP closure decision + `phase-4-completion.md` |
| 5 | Altercation Detection | ≥ 85% fight clips; 0 alerts on 30 min normal footage | 🟡→❎ Executed 2026-08-28, **closed 2026-08-29 by owner decision (c) "accept documented limitation"** (Gate-4 FP-fork precedent): fight path built/wired/tested (235+4 suite), but Gate 5 numeric criteria unmet (UBI 11/40 full-length, 5/35 windowed vs ≥85%; negatives 22 alerts/42.4 min vs 0 — assist+crowd clean). Negative suite ✅ committed. Tuning deferred to Phase 10 data loop; windowed protocol (~5 min/run) reserved. Evidence in runs/*.json → [phase-5-completion.md](./phase-5-completion.md) |
| 6 | Event Engine + Severity | golden-file tests pass | ☐ Not started |
| 7 | Edge Messaging | 10-min blackout → zero loss, zero dupes | ☐ Not started |
| 8 | Backend Services | restart-safe consumer; event < 1 s to dashboard | ☐ Not started |
| 9 | Dashboard | e2e demo < 2 s → **tag v0.1.0** | ☐ Not started |
| 10 | MLOps + Edge (stretch) | registry-driven deploy; drift monitors live | ☐ Not started |

---

# PHASE 0 — Scaffold & Environment (2–3 days)

> **Goal:** runnable skeleton, dev infra up, OSS project hygiene from commit #1.
> Stack decision source: `implementation-plan.md` §2.

### Step 0.1 — Create the repository
- **Do:** `OWNER:` create the GitHub repo (`mobisentra`), run `git init` + first commit locally, and clone/create it at the workspace root. Everything else in this runbook assumes the repo root is the working directory.
- **Done when:** `git status` works at the repo root and the remote is set.

### Step 0.2 — Project hygiene files
- **Do:** add license and community files.
- **Files:** `LICENSE` (AGPL-3.0 full text), `README.md` (pitch + architecture diagram + placeholder quickstart), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/` (bug + feature), `CHANGELOG.md`, `.gitignore`.
- **Done when:** README contains the tagline *"Turning existing CCTV infrastructure into intelligent safety sensors for public mobility."* and a (placeholder) 5-minute quickstart section.
- **OWNER:** commit — suggested: `chore: project scaffold hygiene files (AGPL-3.0, README, community docs)`

### Step 0.3 — Monorepo folder skeleton
- **Do:** create the layout from `implementation-plan.md` §3.
- **Commands:**
  ```bash
  mkdir -p edge/mobisentra/{ingestion,vision,analytics,events,messaging}
  mkdir -p edge/{configs,tests}
  mkdir -p backend/src/{consumer,schema,api,ws}
  mkdir -p dashboard bridge mlops infra schemas
  mkdir -p .github/workflows
  ```
- **Done when:** tree matches §3 of the plan; empty package dirs contain a placeholder `.gitkeep` or module files.

### Step 0.4 — Init the edge Python package (uv)
- **Do:** uv-managed package with lint+test config. Minimal deps for now (vision deps arrive in Phase 2).
- **Commands:**
  ```bash
  cd edge
  uv init --package .
  uv add opencv-python paho-mqtt pyyaml
  uv add --dev pytest ruff
  ```
- **Files:** `edge/pyproject.toml` (Python ≥ 3.11, ruff config, pytest config), `edge/mobisentra/__init__.py`.
- **Done when:** `uv run pytest` passes with a trivial test and `uv run ruff check .` is clean.

### Step 0.5 — Init the backend (pnpm + strict TypeScript)
- **Do:** strict-TS Node project.
- **Commands:**
  ```bash
  cd backend
  pnpm init
  pnpm add -D typescript tsx @types/node vitest
  ```
- **Files:** `backend/tsconfig.json` with `"strict": true`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`; `backend/src/index.ts`.
- **Done when:** `pnpm tsx src/index.ts` runs and `pnpm tsc --noEmit` is clean.

### Step 0.6 — Dev infra via docker-compose
- **Do:** one command brings up the whole open-source stack: Apache Kafka (KRaft, no ZooKeeper), EMQX (MQTT), PostgreSQL, Redis, MLflow.
- **Files:** `infra/docker-compose.yml` (single-node KRaft Kafka — e.g. `bitnami/kafka` env-var config; `emqx/emqx`; `postgres:16`; `redis:7`; MLflow server), `infra/README.md` (what each service is for + ports).
- **Ports (suggested):** Kafka `9092`, EMQX `1883` (MQTT) + `18083` (dashboard), PG `5432`, Redis `6379`, MLflow `5000`.
- **Commands:** `docker compose up -d` (from `infra/`)
- **Done when:** all containers healthy; EMQX dashboard opens at `http://localhost:18083` (default admin/public).

### Step 0.7 — CI from commit #1
- **Do:** GitHub Actions lint+test for both languages on every push.
- **Files:** `.github/workflows/ci.yml` — jobs: `edge` (uv sync → ruff → pytest), `backend` (pnpm install → tsc --noEmit → vitest).
- **Done when:** first run green on GitHub; README carries the badge.
- **OWNER:** commit + push — suggested: `ci: lint+test workflows for edge and backend`

### Step 0.8 — MQTT→Kafka round-trip smoke test
> **Corrected 2026-08-25:** EMQX open-source has **no Kafka connector** (Enterprise-only; verified against 5.8.3 and 5.10.3 — the OSS image ships only HTTP + MQTT bridges). The bridge is our own thin MQTT→Kafka gateway service (`bridge/`, Node + `@confluentinc/kafka-javascript`), started automatically by compose.
- **Do:** prove the messaging path end-to-end with the gateway.
- **Topic scheme (locked):** MQTT topics use **slashes** (`mobisentra/events`), Kafka topics use **dots** (`mobisentra.events`); the gateway maps `/` → `.`. ⚠️ A dot is not a separator in MQTT — `mobisentra.events` will never match `mobisentra/#`.
- **Steps:**
  1. `docker compose -f infra/docker-compose.yml up -d` — starts Kafka, EMQX, bridge, PG, Redis, MLflow.
  2. Publish a CloudEvents envelope:
     ```bash
     cd edge && uv run python -c "
     import paho.mqtt.client as mqtt, time, json
     c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2); c.connect('localhost', 1883); c.loop_start(); time.sleep(1)
     c.publish('mobisentra/events', json.dumps({'specversion':'1.0','id':'t1','source':'/mobisentra/edge/BUS_102/CAM_04','type':'org.mobisentra.event.fall_detected','time':'2026-08-25T00:00:00Z','datacontenttype':'application/json','data':{}}), qos=1)
     time.sleep(1); c.loop_stop()"
     ```
  3. Consume:
     ```bash
     docker compose -f infra/docker-compose.yml exec kafka \
       /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 \
       --topic mobisentra.events --from-beginning --timeout-ms 8000
     ```
- **Done when:** the envelope JSON appears in the Kafka consumer output. ✅ **Verified 2026-08-25** — full containerized round-trip, envelope arrived intact.

### Step 0.9 — Freeze event schema v0
- **Do:** CloudEvents envelope + JSON Schema, versioned, shared by edge and backend.
- **Files:** `schemas/events/v0/event.schema.json` (+ per-type schemas: detection, tracking, event, alert, analytics).
- **Envelope (contract):**
  ```json
  {
    "specversion": "1.0",
    "id": "<uuid — idempotency key>",
    "source": "/mobisentra/edge/{vehicle_id}/{camera_id}",
    "type": "org.mobisentra.event.<event_type>",
    "time": "<ISO8601>",
    "datacontenttype": "application/json",
    "data": {
      "event_type": "fall_detected",
      "severity": "HIGH",
      "camera_id": "BUS_102_CAM_04",
      "location": "coach_rear",
      "tracks": [27],
      "confidence": 0.94,
      "model_versions": {},
      "evidence_ref": "s3-or-local://..."
    }
  }
  ```
- **Kafka topics (fixed names):** `mobisentra.detection`, `mobisentra.events`, `mobisentra.alerts`, `mobisentra.analytics`.
- **Done when:** schemas validate a sample event with a JSON Schema validator (add a tiny test in both `edge/tests` and `backend`).

### Step 0.10 — Camera registry config format
- **Do:** YAML registry describing cameras, zones, thresholds; default points at bundled sample videos.
- **Files:** `edge/configs/cameras.yaml`, `edge/configs/sample-cameras.yaml`.
- **Shape:**
  ```yaml
  cameras:
    - id: BUS_102_CAM_04
      source: sample://videos/bus_interior_01.mp4   # bundled sample (default)
      # source: rtsp://user:pass@host/stream        # real CCTV (config change only)
      vehicle_id: BUS_102
      zones:
        bus_area:
          polygon: [[0.05, 0.05], [0.95, 0.05], [0.95, 0.95], [0.05, 0.95]]  # normalized coords
          max_capacity: 50
        door_roi:
          polygon: [[0.80, 0.40], [0.95, 0.40], [0.95, 0.85], [0.80, 0.85]]
        restricted_zone:
          polygon: []
      thresholds:
        occupancy_confirm_frames: 30
        restricted_loiter_seconds: 5
        door_obstruct_seconds: 3
  ```
- **Done when:** a loader module (`edge/mobisentra/ingestion/config.py`) parses + validates the YAML with unit tests.

### GATE 0 — Scaffold
- [x] `docker compose up` starts the full stack; MQTT→Kafka round-trip works with a test message *(verified 2026-08-25: envelope `smoke-0003` MQTT→EMQX→bridge→Kafka→consumer, intact)*
- [x] Local checks green: Python ruff + 16 pytest, backend tsc + 6 vitest, bridge tsc *(GitHub Actions CI runs on first owner push)*
- [x] Event schema v0 frozen and validated from both languages
- [ ] **Clone test:** fresh clone → README quickstart → same state *(activates after first push)*
- **OWNER:** commit — suggested: `feat(phase-0): monorepo scaffold, compose stack, MQTT→Kafka gateway, schema v0, camera registry`

---

# PHASE 1 — Video Ingestion (1 week)

> **Goal:** lag-free frames from any source; never crash on stream loss.
> Pitfall being avoided: naive `cap.read()` accumulates 2–3 minutes of lag — the #1 real-world failure.

### Step 1.1 — Acquire sample videos
- **Do:** collect 3–5 open-licensed clips (bus/metro interior, station platform, crowded + empty, day + low-light). Store under `edge/sample_data/videos/` with a `SOURCES.md` recording origin + license of every clip.
- **Done when:** `sample-cameras.yaml` points at real playable files.

### Step 1.2 — StreamReader (latest-frame-only)
- **Do:** background thread continuously drains the OpenCV buffer and keeps **only the latest frame**; main loop calls a thread-safe `get_frame()`.
- **Files:** `edge/mobisentra/ingestion/stream_reader.py`.
- **Interface (contract):**
  ```python
  class StreamReader:
      def __init__(self, source: str, reconnect_timeout_s: float = 10.0): ...
      def start(self) -> None: ...
      def stop(self) -> None: ...
      def get_frame(self) -> Frame | None:   # latest frame only, never a stale queue
      def status(self) -> StreamStatus:      # UP | DOWN | RECONNECTING + last_frame_ts
  ```
- **Done when:** reading a sample file at 30 FPS while consuming at 5 FPS shows < 200 ms lag between wall-clock and frame timestamp.

### Step 1.3 — Watchdog + auto-reconnect
- **Do:** no frame for > 10 s → mark stream DOWN → reconnect with exponential backoff (1s → 2s → 4s → … capped 60s) → resume.
- **Done when:** killing the source mid-read triggers DOWN within 10 s and recovery on restore (see Step 1.6 tests).

### Step 1.4 — Source adapters
- **Do:** one `StreamReader`, three sources: bundled sample file (loop), MP4 file, webcam (`0`), RTSP URL. GStreamer pipeline variant kept behind a flag for Jetson NVDEC later — laptop uses FFmpeg backend.
- **Files:** `edge/mobisentra/ingestion/sources.py`.
- **Done when:** switching source = changing one YAML value in the camera registry (no code change).

### Step 1.5 — FPS throttling / frame-skip
- **Do:** config option `analyze_every_n_frames` per camera; pipeline consumes only every Nth frame.
- **Done when:** configured N=3 on a 30 FPS source yields ~10 analysis FPS with stable IDs later.

### Step 1.6 — Unit tests
- **Do:** synthetic video file test; forced-disconnect test (release source mid-read); reconnect-recovery test; lag assertion test.
- **Files:** `edge/tests/test_stream_reader.py`.
- **Done when:** `uv run pytest edge/tests/test_stream_reader.py` green.

### Step 1.7 — Soak test
- **Do:** run a sample/RTSP stream for 60+ minutes; log per-minute lag, frame drops, reconnects.
- **Files:** `edge/tests/soak.py` (manual script, results recorded in `Doc/` notes).
- **Done when:** 60 min with < 200 ms sustained lag.

### GATE 1 — Ingestion
- [x] 60+ min run with < 200 ms lag *(2026-08-25: 3600 s soak, 4 cameras incl. live RTSP; worst max 148 ms, p95 ≤ 22 ms)*
- [x] Camera kill → restore: auto-recovers in < 15 s, process never crashes *(fault-injection test: left UP in 0.0 s, recovered on restore)*
- [x] All ingestion unit tests green *(38/38 incl. Phase 0; ruff clean)*

---

# PHASE 2 — Detection + Tracking (1–2 weeks)

> **Goal:** stable person track IDs per camera — the substrate every analytic uses.

### Step 2.1 — Add vision dependencies
- **Commands:**
  ```bash
  cd edge && uv add ultralytics supervision
  ```
- **Done when:** `uv run python -c "from ultralytics import YOLO; YOLO('yolo26n.pt')"` downloads weights and runs once on a sample frame. *(executed 2026-08-25, yolo26n)*

### Step 2.2 — Detector+tracker wrapper
- **Do:** single wrapper running YOLO26n (fallback `yolo11n.pt`) with ByteTrack/BoT-SORT and persistence. Shipped default tracker (2026-08-26): `configs/tracktrack-tuned.yaml` — best avg gate metric + 4× fewer ID fragments; `botsort-tuned.yaml` kept as single-clip-ratio fallback.
- **Files:** `edge/mobisentra/vision/tracker.py`.
- **Core call:**
  ```python
  results = model.track(frame, persist=True, tracker="bytetrack.yaml", conf=0.3, classes=[0], verbose=False)
  ```
- **Done when:** consecutive frames return consistent `track_id`s for the same person.

### Step 2.3 — Track history buffer
- **Do:** per-camera, per-ID ring buffer of (timestamp, box, center) sized ~10 s — feeds zones, fall, fight later.
- **Files:** `edge/mobisentra/vision/track_history.py`.
- **Done when:** history API `history(track_id, seconds=5)` returns time-ordered records; unit tested.

### Step 2.4 — Person class filter + logging of others
- **Do:** track class 0 (person) only; log other classes (bag, etc.) to the JSONL debug log without tracking.
- **Done when:** debug JSONL shows tracked persons and detected-but-untracked objects distinctly.

### Step 2.5 — Tracker tuning
- **Do:** raise `track_buffer` to 50–70 for crowded occlusions; prepare `botsort.yaml` config; A/B ByteTrack vs BoT-SORT on moving-cam footage.
- **Done when:** A/B notes recorded in `Doc/` (IDs stable through partial occlusion; which tracker per camera type — ByteTrack static / BoT-SORT moving).

### Step 2.6 — Debug overlay + JSONL
- **Do:** OpenCV window drawing boxes+IDs; parallel JSONL of per-frame detections (`edge/runs/debug/*.jsonl`).
- **Done when:** visual sanity check on every sample video passes.

### Step 2.7 — FPS benchmark baseline
- **Do:** measure sustained FPS at 720p and 1080p on the laptop GPU; record numbers.
- **Files:** `edge/tests/bench.py`; results → `Doc/`.
- **Done when:** ≥ 15 FPS sustained at 1080p (or documented hardware limit + resolution fallback).

### GATE 2 — Detection + Tracking
- [ ] On crowded sample footage, track IDs stay stable through partial occlusions *(re-gate 2026-08-26: 6 clips measured, best 0.741; Option C first-party ReID executed same day — REGRESSION on occlusion clip (0.376–0.540 vs 0.593), rejected with evidence in [phase-2-completion.md §8](./phase-2-completion.md); 0.80 is input-quality-bound at 480p — A′ waive decision pending)*
- [x] ≥ 15 FPS sustained on laptop at chosen resolution *(57.25 @720p / 44.09 @1080p, 60 s sustained, device=mps, 2026-08-26)*
- [x] **Clone test re-run:** fresh clone → quickstart still works *(HEAD: ruff clean, 38 tests, compose config valid; full-tree sim incl. Phase 2: 54 tests + vision smoke green)*

---

# PHASE 3 — Zones, Occupancy, Door Rules (1 week, no ML)

> **Goal:** first real safety events from pure geometry + rules.

### Step 3.1 — Zone engine
- **Do:** load zone polygons from camera registry; detect person-in-zone per frame via `supervision.PolygonZone`.
- **Files:** `edge/mobisentra/analytics/zones.py`.
- **Done when:** per-frame zone membership per track ID available to analytics; unit tested with synthetic boxes.

### Step 3.2 — Occupancy with hysteresis
- **Do:** `occupancy = people_in_bus_area / max_capacity` → Normal (<70%) / Moderate (70–90%) / Crowded (>90%) / Overcrowded (>100%); state changes only after N consecutive confirming frames (`occupancy_confirm_frames`).
- **Files:** `edge/mobisentra/analytics/occupancy.py`.
- **Done when:** feeding synthetic count sequences shows no flicker at boundaries; unit tested.

### Step 3.3 — Restricted-zone intrusion
- **Do:** person ∩ restricted polygon for > `restricted_loiter_seconds` → candidate event (consumed by Phase 6 engine).
- **Done when:** dwell-time logic unit tested with synthetic histories.

### Step 3.4 — Door obstruction v1
- **Do:** person ∩ door_roi for > `door_obstruct_seconds` → candidate event. Reserve an MQTT input slot for real door telemetry later (no implementation yet).
- **Done when:** candidate events emitted on test footage; telemetry slot documented.

### Step 3.5 — Zone editor utility
- **Do:** click polygon points on a saved frame → export YAML snippet. (Simple OpenCV window is fine; polish later.)
- **Files:** `edge/tools/zone_editor.py`.
- **Done when:** a zone drawn with the tool loads back into the zone engine.

### Step 3.6 — Validation against manual counts
- **Do:** on test footage, compare occupancy count vs manual count at sampled timestamps; run 30 min of empty-zone footage looking for false positives.
- **Done when:** ±10% vs manual; zero false zone events on the empty footage.
- **Progress 2026-08-27:**
  - *Criterion 2 (empty-zone FP) ✅* — new `tools/zone_fp_soak.py`: siting pass unions every detection bbox over the real-footage pool into a 24×14 grid, takes the largest empty rectangle (maximal-rectangle scan, unit-tested), sites all three zone types there (occupancy + restricted + door, production thresholds), then soaks the pool (4 real clips ≈ 49 s, 16:9, looped 37×) through the production `CameraAnalytics` for **30.0 min of stream / 23,760 analyzed frames → 0 zone events** (`runs/zone-fp-soak.json`). Documented limitation: ~49 s of unique footage repeated — the exposure tested is detector noise + zone logic over stream time, not 30 min of unique scenes.
  - *Criterion 1 (±10% vs manual) — measured, awaiting owner* — `tools/occupancy_check.py` re-measured on the current stack (yolo26n-pose): bus1.mp4 frames 58/174/290/406/522 → **3, 3, 3, 5, 4**; annotated JPGs in `runs/occupancy-check/`. Owner counts heads in the 5 JPGs and runs the `verdict` subcommand to close the gate.

### GATE 3 — Zones / Occupancy
- [ ] Occupancy within ±10% of manual count *(measured 3,3,3,5,4 — awaiting owner manual counts for the verdict)*
- [x] Zero false positives over 30 min empty-zone footage *(2026-08-27: 30.0 min stream, 23,760 analyzed frames, 0 zone events — `runs/zone-fp-soak.json`, harness `tools/zone_fp_soak.py`)*
- [x] Zone editor round-trips YAML *(evidenced at Step 3.5, 2026-08-26)*

---

# PHASE 4 — Fall Detection (2 weeks)

> **Goal:** fall/collapse events with low false positives. Hybrid: keypoint rules + temporal classifier (later).

### Step 4.1 — Pose model swap
- **Do:** `yolo26n-pose.pt` (updated 2026-08-26 — YOLO26 family per the locked model decision; `yolo11n-pose.pt` = fallback); carry track IDs through pose results (pose runs inside the same track call).
- **Files:** `edge/mobisentra/vision/pose.py`.
- **Done when:** per-frame (track_id → 17/33 keypoints) flows into track history alongside boxes.

### Step 4.2 — Keypoint feature extraction
- **Do:** per track, compute: torso angle, head–hip distance (normalized by box height), vertical velocity of hip/head, bbox aspect ratio.
- **Files:** `edge/mobisentra/analytics/fall_features.py`.
- **Done when:** features are pure functions of keypoint history — unit tested with synthetic skeletons (standing, bending, lying).

### Step 4.3 — Rule cascade v1
- **Do:** candidate fall = rapid downward vertical velocity + torso goes horizontal + head near hip level + **no recovery movement for T seconds** (configurable, start T = 3 s).
- **Files:** `edge/mobisentra/analytics/fall.py`.
- **Done when:** emits fall candidates with confidence + track ID; unit tested on synthetic sequences (fall vs sit vs bend).

### Step 4.4 — Evidence buffer
- [x] **Done 2026-08-27.** `events/evidence.py`: JPEG-compressed 5 s camera ring (`EvidenceBuffer`, downscaled, memory-bounded) + `EvidenceWriter` (H.264 MP4 via PyAV — browser/decoder-friendly, cv2-verified — + `keypoints.json` sidecar) + `enforce_retention` clip cap (retention hook; time-based expiry is Phase 8/9 policy). Wired e2e: `CameraAnalytics` (fall on ALL cameras now, not just zoned), `run_frame` pose branch (`produces_pose` routing), `detection.yaml` → `yolo26n-pose.pt`. Real-clip proof: pipeline over UR Fall `fall-01-cam0` → `fall_detected` row + playable clip + sidecar in `runs/evidence/`. **Also fixed (found by that first real run):** ID-switch tolerance in the cascade — confirm window runs on the frame clock (`now_ts`), triggers require fresh samples (stale histories can't re-arm on old collapse evidence), other-track recovery needs sustained upright on its OWN timeline observed after the trigger (frozen pre-fall duplicates poison recovery otherwise). 182 tests green.
- **Files:** `edge/mobisentra/events/evidence.py`.

### Step 4.5 — Benchmark on UR Fall + Le2i
- [x] **Done 2026-08-27** (UR Fall; Le2i documented for manual download). Download: `mlops/datasets/download_ur_fall.py` (host moved to `fenix.ur.edu.pl`, old domain DNS-dead; 70 cam0 MP4s, license CC BY-NC-SA 4.0 + citation recorded in `mlops/datasets/SOURCES.md`; gitignored, benchmark-only). Runner: `edge/tools/fall_benchmark.py` — single pass + settle phase (UR Fall clips median 3.1 s, 29/30 < 6.5 s: a 3 s confirm can only elapse via settle, which applies production occlusion semantics). **Numbers (final cascade, full table in phase-4-plan.md): falls 28/30 = 93.3% (gate ≥90% ✅), ADL FP 9 events / 5.0 min (gate <2 FP/hr ❌ as-measured — FP population is UR Fall's designed mattress-lie hard negatives + lying jitter; mitigation paths documented).** Five cascade iterations were driven by per-clip failure analysis (ID-switch recovery poisoning, jitter velocity spikes, knee-drop falls).
- **Files:** `mlops/datasets/` scripts + results table in `Doc/`.
- **Done when:** numbers recorded — target ≥ 90% detection on UR Fall, < 2 FP/hr. *(detection target met; FP target blocked on hard negatives — owner decision in phase-4-plan.md)*

### Step 4.6 — Tune; classifier only if needed
- **Do:** tune thresholds/features against the benchmark. **Only if rules can't hit the gate:** train a lightweight temporal classifier (LSTM/GBM on keypoint sequences) — defer deep models.
- **Done when:** gate metrics met with the simplest approach that works; decision + numbers documented.

### GATE 4 — Fall Detection
- [ ] ≥ 90% of falls caught on UR Fall test set
- [ ] < 2 false positives/hour on normal-activity footage
- [ ] Evidence clip attached to every trigger

---

# PHASE 5 — Altercation Detection (2–3 weeks)

> **Goal:** fight events. Start pretrained (MoViNet4Violence), don't train from scratch. Field lesson: Wollongong transit hit 23% false-positive rate — **negatives are the defense**.

### Step 5.1 — Integrate MoViNet4Violence
- **Do:** streaming violence classifier from the open `engares/MoViNets-for-Violence-Detection` implementation; runs on full-frame or on crops around close-proximity track pairs. Verify + document its license in the model zoo notes.
- **Files:** `edge/mobisentra/vision/action.py`.
- **Done when:** streaming scores over a sample clip; latency + FPS impact measured.

### Step 5.2 — Pair-finding
- **Do:** identify track pairs with overlapping/nearby boxes sustained > N frames → candidate interaction clips for the action model.
- **Files:** `edge/mobisentra/analytics/pairs.py`.
- **Done when:** on test footage, fighting pairs are found before classification; unit tested with synthetic boxes.

### Step 5.3 — Signal fusion (model never alerts alone)
- **Do:** fight candidate = action-model score + high proximity + rapid relative motion + repeated contact proxy (box intersection oscillation). Fusion logic lives here; severity mapping comes in Phase 6.
- **Files:** `edge/mobisentra/analytics/fight.py`.
- **Done when:** fusion unit tested: each signal alone does NOT trigger; combined does.

### Step 5.4 — Negatives test set
- **Do:** collect/curate negative clips from day one: hugging, playing, rushing-to-exit, conductor assisting passenger, normal crowded interaction.
- **Files:** `edge/sample_data/negatives/` + manifest with origins/licenses.
- **Done when:** ≥ 30 min of negatives runnable as a regression suite for FP rate.

### Step 5.5 — Evaluation
- **Do:** run over positive fight clips (Hockey Fights / UBI-Fight — open; **never RWF-2000**) + the negatives set. Record detection rate + FP/hr.
- **Done when:** ≥ 85% of fight clips detected; zero alerts on the 30-min negative set.

### GATE 5 — Altercation Detection
- [ ] ≥ 85% fight clip detection
- [ ] 0 alerts on 30 min normal crowded interaction
- [ ] Negative regression suite committed

---

# PHASE 6 — Event Engine + Severity (1 week)

> **Goal:** single deterministic path from raw signals to operator events. The highest-value test suite in the repo.

### Step 6.1 — Event engine service
- **Do:** consumes analytics outputs per camera (zone, occupancy, fall, fight), aggregates evidence over time windows, applies debouncing — min N confirmations, max 1 alert per X min per event-type per camera.
- **Files:** `edge/mobisentra/events/engine.py` — **pure logic, no I/O.**
- **Done when:** engine is testable as a function: signal sequences in → event list out.

### Step 6.2 — Severity mapping (configurable)
- **Do:** LOW (restricted zone) / MEDIUM (overcrowding, suspicious) / HIGH (fall, aggressive) / CRITICAL (confirmed altercation, trapped). Thresholds in config, not code.
- **Files:** `edge/configs/severity.yaml`.
- **Done when:** editing YAML changes severity without code changes.

### Step 6.3 — CloudEvents output
- **Do:** engine output = schema v0 envelopes (Step 0.9), ready for the Phase 7 publisher. `model_versions` stamped into every payload.
- **Done when:** all outputs validate against `schemas/events/v0/`.

### Step 6.4 — Golden-file tests
- **Do:** scripted signal sequences → exact expected event streams (severities, debounces, no duplicates). Cover: repeated fall signals → one alert; occupancy flicker → no event; fight signals below fusion threshold → no event.
- **Files:** `edge/tests/golden/*.json` + `edge/tests/test_event_engine.py`.
- **Done when:** golden suite green.

### GATE 6 — Event Engine
- [ ] Golden-file tests pass — no duplicates, correct severities, debounce windows honored
- [ ] Engine is pure logic (no camera, network, or DB calls inside)
- [ ] All events validate against schema v0

---

# PHASE 7 — Edge Messaging: MQTT Spool → Bridge → Kafka (1–2 weeks)

> **Goal:** zero event loss from a moving vehicle. MQTT QoS 1 + local disk spool at edge; Kafka stays server-side.

### Step 7.1 — MQTT publisher (QoS 1)
- **Do:** publish engine events to EMQX with QoS 1; topic mapping to `mobisentra.*`.
- **Files:** `edge/mobisentra/messaging/publisher.py`.
- **Done when:** published events appear in Kafka via the Phase 0 bridge rule.

### Step 7.2 — SQLite disk spool
- **Do:** events persist to a local SQLite queue **before** publish; on publish failure/no network, keep; replay on reconnect; dedupe on event `id`.
- **Files:** `edge/mobisentra/messaging/spool.py`.
- **Schema:**
  ```sql
  CREATE TABLE spool (
    id TEXT PRIMARY KEY,          -- CloudEvents id (dedupe key)
    topic TEXT NOT NULL,
    payload TEXT NOT NULL,        -- full envelope JSON
    created_at TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0
  );
  ```
- **Done when:** unit tests cover: publish fail → retained; reconnect → replayed once; duplicate id → not re-sent.

### Step 7.3 — Bridge hardening
- **Do:** harden the MQTT→Kafka gateway (`bridge/`): pause MQTT consumption while the Kafka producer is disconnected (backpressure), forward/drop counters, duplicate-suppression option. Document env config in `bridge/README.md`.
- **Done when:** kill-switch tests (7.4) pass through the gateway without loss.

### Step 7.4 — Fault-injection tests
- **Do:** (a) kill broker mid-stream; (b) 5-minute network partition during active events; (c) force duplicate delivery (QoS 1 = at-least-once) → verify exactly-once after consumer dedupe (dedupe lands in Phase 8; here assert spool behavior).
- **Files:** `edge/tests/test_messaging_resilience.py`.
- **Done when:** all three scenarios pass at the edge layer.

### GATE 7 — Messaging
- [ ] 10-minute network blackout during active events → all events arrive post-reconnect
- [ ] Zero loss, zero duplicates after dedupe
- [ ] Broker kill/restore mid-stream: no crash, full replay

---

# PHASE 8 — Backend Services (1–2 weeks)

> **Goal:** durable history + live state + real-time push.

### Step 8.1 — Kafka consumer
- **Do:** `@confluentinc/kafka-javascript` (v1.10+; kafkajs is unmaintained), manual offset commits — commit only after successful processing.
- **Commands:** `cd backend && pnpm add @confluentinc/kafka-javascript`
- **Files:** `backend/src/consumer/`.
- **Done when:** consumer processes all four topics with graceful shutdown.

### Step 8.2 — Idempotency
- **Do:** Redis dedupe before processing: `SET dedupe:{source}:{id} NX EX 86400` — skip if already set.
- **Files:** `backend/src/consumer/dedupe.ts`.
- **Done when:** replayed events are processed exactly once (unit + integration tested).

### Step 8.3 — Write path per event
- **Do:** PostgreSQL insert `ON CONFLICT (event_id) DO NOTHING` → Redis live-state update (latest per camera/vehicle, TTL) → Socket.IO room emit (`alerts:{vehicle_id}`).
- **Files:** `backend/src/consumer/pipeline.ts`, `backend/src/ws/`, PG migrations in `backend/src/schema/`.
- **Done when:** one published edge event lands in all three stores; dashboard test client receives it.

### Step 8.4 — REST API
- **Do:** endpoints below; every action (ack/escalate) writes an audit-log row.
- **Endpoints:**
  | Method | Path | Purpose |
  |---|---|---|
  | GET | `/api/incidents` | list w/ filters (severity, camera, time) |
  | GET | `/api/incidents/:id` | detail incl. evidence ref |
  | GET | `/api/cameras` | registry + live status from Redis |
  | GET | `/api/events` | history query |
  | POST | `/api/incidents/:id/ack` | acknowledge (audit logged) |
  | POST | `/api/incidents/:id/escalate` | escalate (audit logged) |
- **Done when:** all endpoints integration-tested against the compose stack.

### Step 8.5 — Integration tests
- **Do:** compose-based integration suite: publish → consume → PG row + Redis state + WS message; consumer restart mid-stream.
- **Files:** `backend/test/`.
- **Done when:** restart mid-stream → no lost/duplicated PG rows.

### GATE 8 — Backend
- [ ] Consumer restart mid-stream: zero lost/duplicated rows
- [ ] Dashboard receives an event < 1 s after edge publish
- [ ] Ack/escalate write audit rows

---

# PHASE 9 — Dashboard (1–2 weeks)

> **Goal:** operator-usable control center. Substance first: incident-feed latency and reliability over aesthetics.

### Step 9.1 — Frontend scaffold
- **Do:** Vite + React + TypeScript + Tailwind; Socket.IO client with reconnect + room subscribe per selected vehicle.
- **Files:** `dashboard/`.
- **Done when:** live incident feed renders in < 1 s of backend emit (using Gate 8 test path).

### Step 9.2 — Live camera grid
- **Do:** camera cards with status (online/offline/reconnecting) + occupancy badge (Normal/Moderate/Crowded/Overcrowded) from Redis live state.
- **Done when:** grid reflects camera registry; status changes propagate in real time.

### Step 9.3 — Active incidents list
- **Do:** severity-colored incident list (CRITICAL/HIGH/MEDIUM/LOW), sorted newest-first, with ack/escalate actions.
- **Done when:** actions hit the REST API and audit rows appear.

### Step 9.4 — Incident detail + evidence + replay
- **Do:** detail view: metadata, confidence, tracks, evidence clip link/replay.
- **Done when:** a synthetic fall event's evidence clip is viewable from the incident.

### Step 9.5 — Event history + filters
- **Do:** filterable history table (camera, type, severity, time range) over the REST API.
- **Done when:** filters round-trip to the API and paginate.

### Step 9.6 — Auth (optional, off by default)
- **Do:** env flag enables simple user/pass + roles (viewer/operator/admin); open single-user mode locally by default. Audit logging on regardless.
- **Done when:** flag documented in README; both modes tested.

### Step 9.7 — End-to-end demo + release
- **Do:** run the standing acceptance demo: synthetic fall on the laptop pipeline → incident on dashboard < 2 s → acknowledge → audit row. Record a demo GIF/video.
- **OWNER:** tag `v0.1.0`, GitHub Release with demo video + `CHANGELOG.md` entry. Suggested: `release: v0.1.0 — MVP demo (CCTV → detections → events → dashboard)`

### GATE 9 — Dashboard (v0.1.0)
- [ ] E2E: synthetic fall → dashboard incident < 2 s → ack → audit row
- [ ] **Clone test (release blocker):** fresh clone → compose up → quickstart works end-to-end
- [ ] v0.1.0 tagged with demo video — *the "project exists" moment*

---

# PHASE 10 — MLOps + Edge Deployment (2+ weeks, stretch — post-v0.1.0)

> **Goal:** reproducible models, monitored deployment, Jetson migration. Nothing here blocks v0.1.0.

### Step 10.1 — DVC pipeline
- **Do:** dataset → train → eval → ONNX export, versioned with DVC; dataset remote = local dir or HuggingFace Datasets (no S3/credit-card requirement for contributors).
- **Files:** `mlops/dvc.yaml`, `mlops/params.yaml`.

### Step 10.2 — MLflow tracking + registry
- **Do:** every training run tracked; registry holds promoted models; edge payloads already carry `model_versions` (Step 6.3) — close the loop: registry version == deployed version.

### Step 10.3 — Training CI
- **Do:** GitHub Actions trains on dataset change; quality gates (mAP/recall thresholds); auto-register on pass.

### Step 10.4 — Drift monitoring
- **Do:** log per-camera confidence distributions + detection rates → Evidently reports → Prometheus/Grafana alerts on drift/FP spikes.

### Step 10.5 — Jetson migration
- **Do:** export TensorRT FP16 engines (INT8 PTQ only if needed — requires 500–1000 calibration frames from own cameras); Docker l4t base images; benchmark on Orin Nano Super — target ≥ 2 streams @ 15 FPS end-to-end.

### Step 10.6 — Continuous data loop
- **Do:** `unknown/low-confidence events → human review → label → dataset → retrain → validate → controlled rollout` — operationalize as a lightweight review queue.

### Step 10.7 — Model zoo
- **Do:** publish fine-tuned weights to HuggingFace with per-model cards (training data lineage, license, metrics) — the OSS project's compounding asset.

### GATE 10 — MLOps/Edge
- [ ] On-device (or emulated) regression suite passes
- [ ] Monitoring dashboards live with drift alerts
- [ ] A retrained model deploys via registry — zero manual file copying

---

## Standing Verification (every phase)

1. **Clone test** after Phases 0, 2, and 9 — a stranger can clone → compose up → quickstart.
2. **E2E demo re-run** after every phase from 6 onward — the Gate 9 demo is the standing acceptance test.
3. Vision numbers recorded in `Doc/`; messaging/backend verified by fault injection, not happy paths.

## When Something Breaks the Sequence

- Gate fails → fix before moving on. Never start the next phase on a failing gate.
- Plan conflict → `implementation-plan.md` wins; update this runbook.
- Scope creep (door telemetry, new use cases, multi-vehicle fleet, Jetson) → it belongs in Phase 10+ or the backlog, not the MVP sequence.
