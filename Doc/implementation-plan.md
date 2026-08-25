# MobiSentra AI — Implementation Plan

> Step-by-step build plan derived from `Doc/plan.md` and web research (Aug 2026).
> **Project type: personal open-source project** — solo-maintained, community-ready, portfolio-grade.
> Target: laptop GPU first → NVIDIA Jetson edge later (stretch).
> MVP slice: **CCTV → YOLO26n → ByteTrack/BoT-SORT → zones/occupancy → fall → fight → MQTT/Kafka → Node.js → React dashboard.**
> Deltas vs a product/company build: license-first decisions, everything runnable by a stranger with no cameras, fully open-source infra, no multi-tenant/operator enterprise features.

---

## 0. Open-Source Ground Rules

### Project Name

**MobiSentra AI** — *AI Vision Intelligence for Safer Public Mobility* (Mobi = mobility, Sentra = sensing + sentinel, AI = vision intelligence).

- **GitHub description:** "MobiSentra AI — AI-powered computer vision platform for real-time safety monitoring across buses, metros, trains, and public transit infrastructure."
- **README tagline:** "Turning existing CCTV infrastructure into intelligent safety sensors for public mobility."
- Web search (Aug 2026) found no obvious existing match for "MobiSentra AI" / "MobiSentra" (nearest hit: safentra.co.uk — different name/market).
- ⚠️ Caveat: an empty web search does **not** guarantee trademark or domain availability. Before any commercial use, check trademark databases (IP India, USPTO, EUIPO) and domains (`mobisentra.ai`, `mobisentra.io`, …). For a project/research/PoC name it's clear to proceed.

### License

**Repo license: AGPL-3.0.** The core dependency `ultralytics` (YOLO11) is AGPL-3.0 — building the pipeline on it makes AGPL the simplest clean path for an open-source repo. Alternatives (paying Ultralytics, or runtime-only via exported ONNX with no ultralytics code) are possible later if ever needed.

Key dependency licenses to respect (spot-check before adding anything new):

| Dependency | License | Note |
|---|---|---|
| ultralytics (YOLO11) | AGPL-3.0 | Drives repo license choice |
| ByteTrack | MIT | Fine |
| supervision (Roboflow) | Apache-2.0 | Fine |
| Apache Kafka / EMQX / PostgreSQL / Redis | Open (Apache/BSD-ish) | Fine |
| MoViNet4Violence weights | Check model card on HF | Document in model zoo |
| RWF-2000 dataset | **Non-commercial** | Never bundle; use Hockey Fights / UBI-Fight |
| Redpanda | BSL (source-available) | **Avoid as default** — use Apache Kafka KRaft instead |

### Principles

1. **Runs-without-hardware:** the default demo input is bundled/sample video + public datasets. A contributor with zero cameras, zero Jetson, zero real RTSP must be able to clone → `docker compose up` → see detections on the dashboard. Real CCTV/RTSP is a config change, not a requirement.
2. **Sample-data-first:** dataset download scripts pull open datasets (UR Fall, Le2i, Hockey Fights, UBI-Fight). Own-vehicle footage collection is an optional extension, never a blocker.
3. **Fully open stack:** every component in the default compose file is OSI-open licensed. No source-available defaults.
4. **CI from day one:** lint + tests on GitHub Actions (free tier) from the first commit; README badge lives or dies with it.
5. **Responsible use:** this is safety-event detection, not surveillance of people. See §10.

### Community assets (created in Phase 0, maintained forever)

- `README.md` — pitch, architecture diagram, 5-minute quickstart, demo GIF
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue templates
- `CHANGELOG.md` + semver tags; GitHub Releases with demo video per release
- `docs/` — architecture decisions (ADRs), dataset licensing notes, model zoo table

---

## 1. MVP Scope (do not exceed)

| # | Capability | Approach |
|---|-----------|----------|
| 1 | Person detection + tracking | YOLO26n + ByteTrack/BoT-SORT (pretrained) |
| 2 | Crowd / occupancy | Zone counting + rules (no ML) |
| 3 | Fall / collapse detection | Pose keypoints + temporal rules → classifier later |
| 4 | Altercation detection | Pretrained MoViNet4Violence → fine-tune later |

Later phases (post-MVP): door obstruction, restricted-zone intrusion, abandoned object, platform safety, emergency crowd movement, seat/zone occupancy.

---

## 2. Locked Tech Decisions (research-validated, Aug 2026)

| Layer | Decision | Rationale |
|---|---|---|
| Detection model | **YOLO26n** (updated 2026-08-26, Phase 2) | +2.4 mAP, −31% CPU ONNX latency, NMS-free end-to-end, smaller than YOLO11n; A/B-validated on the gate clip (0.741 vs 0.679 flicker-filtered). **Fallback: `yolo11n.pt` = one-line config change** (measured, kept working). RT-DETR rejected (slower on edge NPUs) |
| Tracker | **ByteTrack** (static cams) / **BoT-SORT** (bus interiors) | Bus cameras move → BoT-SORT camera-motion compensation. `persist=True` mandatory |
| Pose | **YOLO11-pose** | Same ultralytics stack = simplest MVP. RTMPose-m if throughput headroom needed later (430+ FPS GPU) |
| Fall detection | **Hybrid: keypoint rules + temporal classifier** | OmniFall benchmark: hybrid beats end-to-end on edge. Datasets: UR Fall + Le2i (open) |
| Fight detection | **MoViNet4Violence pretrained** (start) → **VideoMAE fine-tune** (later) | Streaming mode, ~50M params, 5.1MB INT8, open license. ⚠️ RWF-2000 is **non-commercial restricted** — use Hockey Fights / UBI-Fight for commercial work |
| RTSP ingestion | **Threaded StreamReader** (latest-frame-only + watchdog + auto-reconnect) | Naive `cap.read()` accumulates 2–3 min lag — #1 real-world pitfall |
| Edge→cloud messaging | **MQTT QoS 1 + local disk spool (SQLite) → own MQTT→Kafka gateway service → Apache Kafka (server)** | Vehicles have intermittent connectivity; Kafka-on-vehicle loses data during network transitions. Kafka runs server-side behind a gateway. **Corrected 2026-08-25:** the planned EMQX rule-engine Kafka bridge is Enterprise-only (verified: OSS 5.8.3/5.10.3 ship no Kafka connector), so the bridge is our own thin Node gateway (`bridge/`) — stack stays fully open. MQTT topics use slashes, Kafka dots (`mobisentra/events` → `mobisentra.events`) |
| Kafka client (Node) | **@confluentinc/kafka-javascript v1.10+** | kafkajs unmaintained since Feb 2023. Confluent client = librdkafka-backed, KafkaJS-compat mode exists |
| Event schema | **CloudEvents envelope + JSON Schema, versioned** | `id` + `source` = idempotency key; Redis `SETNX` dedupe on consumer |
| Backend | **Node.js + TypeScript** → PostgreSQL (history) + Redis (live state) + Socket.IO (dashboard) | Canonical pattern; PG with `INSERT ... ON CONFLICT` idempotency |
| Frontend | **React + TypeScript + Tailwind**, Socket.IO rooms per vehicle | Redis adapter for multi-instance scale |
| Postprocessing | **supervision** (Roboflow) | Zones, line-crossing, trackers, annotations — battle-tested |
| Edge runtime (MVP) | **Custom Python pipeline** (ultralytics + supervision + paho-mqtt) | DeepStream Python bindings deprecated (DS 9.0+); Service Maker only if fleet scale 10+ vehicles |
| Model optimization | **ONNX → TensorRT FP16** first; INT8 PTQ only if needed | FP16 = 1.8× speedup, zero calibration. INT8 needs 500–1000 calibration frames from own cameras |
| MLOps | **DVC + MLflow + GitHub Actions + Evidently + Prometheus/Grafana** | Standard 2026 stack |
| Edge hardware (prod) | **Jetson Orin Nano Super (~$399)** | 4–6 concurrent 1080p streams w/ YOLOn — fits 2–4 cameras/vehicle |

---

## 3. Repository Structure

```
mobisentra/
├── Doc/                        # plans (existing), research notes
├── edge/                       # Python — runs on vehicle/edge GPU
│   ├── pyproject.toml          # uv-managed
│   ├── mobisentra/
│   │   ├── ingestion/          # StreamReader (RTSP/MP4/webcam)
│   │   ├── vision/             # detector, tracker, pose wrappers
│   │   ├── analytics/          # zones, occupancy, fall, fight
│   │   ├── events/             # event engine, severity, evidence buffer
│   │   ├── messaging/          # MQTT publisher + disk spool
│   │   └── main.py             # pipeline orchestrator
│   ├── configs/                # camera registry, zones (polygons), thresholds
│   └── tests/
├── backend/                    # Node.js + TypeScript
│   ├── src/
│   │   ├── consumer/           # Kafka → PG/Redis/Socket.IO
│   │   ├── schema/             # CloudEvents + JSON Schemas
│   │   ├── api/                # REST (history, incidents)
│   │   └── ws/                 # Socket.IO server
│   └── package.json
├── dashboard/                  # React + TS + Tailwind
├── bridge/                     # MQTT→Kafka bridge config (EMQX rule-engine)
├── mlops/                      # DVC pipelines, MLflow, GH Actions, drift monitoring
├── infra/                      # docker-compose (dev), Jetson deploy files
└── schemas/                    # shared event JSON Schemas (edge + backend)
```

---

## 4. Phases

### Phase 0 — Scaffold & Environment (2–3 days)

**Goal:** runnable skeleton, dev infra up, OSS project hygiene from commit #1.

1. Public GitHub repo (owner runs all git ops) with `LICENSE` (AGPL-3.0), `README.md` (pitch + placeholder quickstart), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/`.
2. Create monorepo layout above. Python via `uv`, Node via `pnpm`, strict TS config.
3. `infra/docker-compose.yml`: Apache Kafka (KRaft, no ZooKeeper), EMQX (MQTT), PostgreSQL, Redis, MLflow. The whole stack must come up with one command.
4. GitHub Actions CI: lint + unit tests for both Python and Node, on every push. README badge.
5. Verify stack: publish one fake MQTT msg → bridge → Kafka topic → consume with Node script.
6. Freeze event schema v0 (CloudEvents envelope): `mobisentra.detection`, `mobisentra.events`, `mobisentra.alerts`, `mobisentra.analytics` topics.
7. Camera registry config format (YAML): id, rtsp_url, codec, zones, thresholds. Include a `sample-cameras.yaml` pointing at bundled sample videos.

**Gate:** `docker compose up` → MQTT→Kafka→consumer round-trip works with test message; CI green; a stranger could clone and follow the quickstart.

---

### Phase 1 — Video Ingestion (1 week)

**Goal:** lag-free frames from any source; never crash on stream loss.

1. Implement `StreamReader`: background thread drains buffer, keeps **latest frame only**, thread-safe `get_frame()`.
2. Watchdog: no frame > 10s → mark stream down → auto-reconnect with backoff.
3. Sources: bundled sample clips (default — anyone can run without hardware), MP4 file, webcam (quick test), RTSP (real). GStreamer pipeline variant kept for Jetson (NVDEC hw decode) — plain FFmpeg backend for laptop.
4. FPS throttling / frame-skip option (analyze every Nth frame).
5. Unit tests: synthetic video file, forced disconnect (kill stream mid-read), reconnect recovery.

**Gate:** RTSP stream runs 60+ min with < 200ms lag; camera kill/restore auto-recovers in < 15s.

---

### Phase 2 — Detection + Tracking (1–2 weeks)

**Goal:** stable person track IDs per camera.

1. Wrap `ultralytics` YOLO11n: `model.track(frame, persist=True, tracker="bytetrack.yaml", conf=0.3)`.
2. Track history buffer per ID (centers, boxes, timestamps) — feeds all analytics later.
3. Person class filter only (class 0); log others (bag, etc.) but don't track yet.
4. Raise `track_buffer` to 50–70 for crowded occlusions. Try BoT-SORT config for moving-cam footage; A/B both.
5. Emit per-frame detections to debug overlay (OpenCV window) + JSONL log.
6. Benchmark: FPS on laptop GPU at 720p/1080p; record baseline numbers.

**Gate:** on crowded sample footage, track IDs stay stable through partial occlusions; ≥ 15 FPS sustained on laptop.

---

### Phase 3 — Zones, Occupancy, Door Rules (1 week, no ML)

**Goal:** first real safety events from pure geometry + rules.

1. Zone config per camera: named polygons (bus_area, door_roi, restricted_zone) in normalized coords.
2. Use `supervision` (`PolygonZone`) for person-in-zone detection.
3. **Occupancy:** `people_in_zone / max_capacity` → Normal (<70%) / Moderate (70–90%) / Crowded (>90%) / Overcrowded (>100%). Hysteresis: require N consecutive frames before state change (kills flicker).
4. **Restricted zone:** person ∩ polygon for > threshold → event.
5. **Door obstruction (v1):** person ∩ door_roi for > threshold → event (door telemetry integration later — MQTT input slot reserved).
6. Zone editor utility: click polygon points on a saved frame, export YAML.

**Gate:** on test footage — occupancy count matches manual count ±10%; zone events fire with zero false positives over 30 min of empty-zone footage.

---

### Phase 4 — Fall Detection (2 weeks)

**Goal:** fall/collapse events with low false positives.

1. Swap detector to `yolo11n-pose.pt`; carry track IDs through pose results.
2. Per-track keypoint features: torso angle, head-hip distance (normalized by box height), vertical velocity, aspect ratio of bbox.
3. Rule cascade v1: rapid downward vertical velocity + torso goes horizontal + head near hip level + **no recovery movement for T seconds** → fall candidate.
4. Evidence buffer: keep rolling ~5s of frames per track; on trigger, snapshot clip + keypoints for the event payload (evidence, per plan §14).
5. Test against UR Fall + Le2i datasets (open access). Measure: detection rate, false positives/hr.
6. Only if rules insufficient: train lightweight temporal classifier (LSTM/GBM on keypoint sequences) — defer deep models.

**Gate:** ≥ 90% falls caught on UR Fall test set with < 2 false positives/hour on normal-activity footage.

---

### Phase 5 — Altercation Detection (2–3 weeks)

**Goal:** fight events; start pretrained, don't train from scratch yet.

1. Integrate **MoViNet4Violence** (`engares/MoViNets-for-Violence-Detection`, open license): streaming classifier on full-frame or on crop around close-proximity track pairs.
2. Pair-finding: tracks with overlapping/nearby boxes sustained > N frames → candidate clip source.
3. Combine signals in event engine: action-model score + proximity + rapid relative motion + repeated contact proxy (box intersection oscillation) → severity, per plan §8. Model alone never alerts.
4. Collect negatives from day one: hugging, playing, rushing-to-exit, conductor assisting — the field FP rate in the Wollongong transit deployment was 23%; negatives are the defense.
5. (Post-MVP) Fine-tune VideoMAE-base on own clip dataset — Hockey Fights / UBI-Fight (open) + own collected data. Track in MLflow.

**Gate:** on test clips: fight clips detected ≥ 85%; zero alerts on 30 min of normal crowded interaction footage.

---

### Phase 6 — Event Engine + Severity (1 week)

**Goal:** single deterministic path from raw signals to operator events.

1. Event engine service: consumes analytics outputs (per-camera), aggregates evidence over time windows, applies debouncing (min N confirmations / max 1 alert per X min per type per camera).
2. Severity mapping (configurable per operator): LOW (restricted zone) / MEDIUM (overcrowding) / HIGH (fall, aggressive) / CRITICAL (confirmed altercation, trapped).
3. Output = CloudEvents JSON: event_type, severity, camera_id, location, tracks, confidence, timestamp, evidence ref.
4. Unit-test the engine as pure logic: signal sequences in → events out. This is the highest-value test suite in the repo.

**Gate:** golden-file tests pass — scripted signal sequences produce exactly the expected event streams, no duplicates, correct severities.

---

### Phase 7 — Edge Messaging: MQTT Spool → Bridge → Kafka (1–2 weeks)

**Goal:** zero event loss from a moving vehicle.

1. Edge publisher: MQTT QoS 1 to broker; **disk spool (SQLite queue) before publish** — on publish failure or no network, persist; replay on reconnect; dedupe via event `id`.
2. Bridge: MQTT→Kafka gateway (`bridge/` service — EMQX OSS has no Kafka connector), mapping MQTT `mobisentra/<segment>` to Kafka `mobisentra.<segment>`. Runs in the local compose stack for dev/demo; same service deploys to any VPS later — no cloud dependency.
3. Kill-switch tests: broker down mid-stream, network partition 5 min, duplicate delivery (QoS 1 = at-least-once) → verify the server receives each event exactly once after dedupe.

**Gate:** 10 min network blackout during active events → all events arrive post-reconnect, zero loss, zero duplicates after consumer dedupe.

---

### Phase 8 — Backend Services (1–2 weeks)

**Goal:** durable history + live state + real-time push.

1. Kafka consumer (`@confluentinc/kafka-javascript`), manual offset commits.
2. Idempotency: Redis `SET dedupe:{source}:{id} NX EX 86400` before processing.
3. Write path per event: PostgreSQL insert (`ON CONFLICT (event_id) DO NOTHING`) → Redis live-state update (latest per camera/vehicle, TTL) → Socket.IO room emit (`alerts:{vehicle_id}`).
4. REST API: incidents list/detail, cameras, history query, acknowledge/escalate actions (with audit log rows).
5. Migrations, strict TS, integration tests against docker-compose stack (testcontainers or compose-based).

**Gate:** restart consumer mid-stream → no lost/duplicated DB rows; dashboard receives event < 1s after edge publish.

---

### Phase 9 — Dashboard (1–2 weeks)

**Goal:** operator-usable control center.

1. Screens: live camera grid (status + occupancy badges), active incidents list (severity colors, ack/escalate), incident detail (metadata, confidence, tracks, evidence clip link, replay), event history w/ filters.
2. Socket.IO client with reconnect + room subscribe per selected vehicle.
3. Auth optional and off by default (personal project): single-user open mode locally; env flag enables simple user/pass + role separation (viewer/operator/admin) for anyone self-hosting seriously. Audit logging on actions regardless.
4. Dark-ops-room styling, but substance first: latency and reliability of the incident feed over aesthetics.

**Gate:** end-to-end demo: trigger synthetic fall event on laptop pipeline → incident appears on dashboard < 2s → operator acknowledges → audit row written.

---

### Phase 10 — MLOps + Edge Deployment (2+ weeks, post-MVP-demo)

**Goal:** reproducible models, monitored deployment, Jetson migration.

1. DVC pipeline: dataset → train → eval → ONNX export; dataset remote = local dir or HuggingFace Datasets (no S3/credit-card requirement for contributors).
2. MLflow tracking + model registry; every edge model version stamped into event payloads.
3. GitHub Actions: train on dataset change, quality gates (mAP/recall thresholds), auto-register.
4. Drift monitoring: log per-camera confidence distributions + detection rates → Evidently reports → Prometheus/Grafana alerts on drift/FP spikes.
5. Jetson: export TensorRT FP16 engines, Docker (l4t base), benchmark on Orin Nano Super — target ≥ 2 streams @ 15 FPS end-to-end.
6. Continuous data loop: `unknown/low-confidence events → human review → label → dataset → retrain → validate → controlled rollout`.
7. **Model zoo:** publish fine-tuned weights to HuggingFace with per-model cards (training data lineage, license, metrics) — this is the OSS project's compounding asset.

**Gate:** on-device regression suite passes; monitoring dashboards live; a retrained model deploys via registry without manual file copying.

---

## 5. Timeline Summary

| Phase | Weeks | Cumulative |
|---|---|---|
| 0 Scaffold | 0.5 | 0.5 |
| 1 Ingestion | 1 | 1.5 |
| 2 Detection+Tracking | 1.5 | 3 |
| 3 Zones/Occupancy | 1 | 4 |
| 4 Fall | 2 | 6 |
| 5 Fight | 2.5 | 8.5 |
| 6 Event Engine | 1 | 9.5 |
| 7 Edge Messaging | 1.5 | 11 |
| 8 Backend | 1.5 | 12.5 |
| 9 Dashboard | 1.5 | 14 |
| 10 MLOps/Edge *(stretch)* | 2+ | 16+ |

**Release milestones:** Phase 9 gate = **v0.1.0** (tag + GitHub Release with demo video — this is the "project exists" moment). Phase 10 completes v0.2.0+. Jetson hardware is optional/stretch; the project must be complete and demoable without it.

Roughly **14 weeks to full MVP demo (v0.1.0)**, 16+ with MLOps/edge hardening.

---

## 6. Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| RTSP buffer lag | Silent multi-min delay | StreamReader pattern from day one (Phase 1), watchdog alerts |
| False positives in field (Wollongong: 23%) | Operator trust collapse | Evidence aggregation (Phase 6), negatives collection from day one, debounce windows |
| ID swaps in dense crowds | Wrong track evidence | BoT-SORT A/B, raised track_buffer, tune match_thresh |
| Dataset licensing (RWF-2000 non-commercial) | Legal blocker | Use Hockey Fights / UBI-Fight / own data for commercial models |
| Network loss on vehicle | Lost events | MQTT QoS 1 + SQLite spool (Phase 7), tested blackout scenario |
| Night/low-light degradation | Missed events | Fine-tune YOLO11n on own low-light footage (Phase 10 loop); monitor per-camera detection rates |
| License contamination (AGPL deps, RWF-2000, BSL tools) | Project unusable/commercially poisoned | AGPL-3.0 repo; dependency license spot-check before adding; open datasets only; document every model weight's license in the model zoo |
| Solo-maintainer burnout | Project stalls | Phase gates keep scope honest; "runs without hardware" keeps contributions possible; Jetson/multi-vehicle marked stretch, not core |
| Privacy/regulatory | Deployment blocker | Privacy checklist below, enforced from MVP |

## 7. Privacy Checklist (build in, not bolt on)

- [ ] No identity recognition; no name/face enrollment; no sensitive-attribute inference from appearance
- [ ] Only event clips + metadata leave the vehicle (never continuous video)
- [ ] Face blur on evidence clips where identification isn't required
- [ ] Event retention per operator policy (auto-expiry)
- [ ] Encryption in transit (MQTT/TLS, Kafka TLS) and at rest (PG disks, spool DB)
- [ ] Role-based access + full audit log of dashboard actions
- [ ] Human review required before CRITICAL escalations

## 8. Verification Doctrine

- Every phase has a hard gate; no phase starts before the previous gate passes.
- Vision phases measured on fixed test footage + public datasets (UR Fall, Le2i, Hockey/UBI) with numbers recorded in `Doc/`.
- Messaging/backend phases tested by fault injection (kill broker, partition network, restart consumer).
- End-to-end demo re-run after every phase — the Phase 9 gate is the standing acceptance test.
- **Clone test:** after Phases 0, 2, 9 — fresh clone → compose up → quickstart works. If a stranger can't run it, it's broken.

---

## 9. Community & Release Cadence

- Semver tags; `CHANGELOG.md` entry per merged feature; GitHub Release per milestone with a short demo video/GIF.
- Label `good first issue` on genuinely small items (zone editor polish, dashboard widgets, docs) — keep 3–5 open at all times after v0.1.0.
- ADRs (`docs/adr/`) for every locked decision in §2 — future contributors (and future you) see *why*, not just *what*.
- Roadmap lives in GitHub Projects/Issues, not just this doc — this doc is the blueprint, issues are the execution truth.

## 10. Responsible Use Policy

Public, linked from README. Core stance:

- MobiSentra detects **safety events, not identities**. No facial recognition, no re-identification across cameras/vehicles, no demographic inference — enforced in code (identity features never extracted), not just policy.
- Intended for transit operators and safety research. Documented misuse category (covert surveillance of individuals) explicitly out of scope; license + policy state it.
- Privacy defaults (§7) are non-negotiable defaults: face blur on evidence clips, retention expiry, no continuous video egress.

This section is a feature, not bureaucracy — it's what makes a surveillance-adjacent OSS project credible and referenceable.
