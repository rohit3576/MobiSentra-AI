# Phase 6 — Event Engine + Severity · Plan

> **Status: APPROVED 2026-08-29 (owner: "ok start the phase 6.1a" — defaults
> locked as proposed); execution one-by-one per the working agreement.**
> Source of truth: `implementation-sequence.md` Phase 6 (on conflict, the
> runbook + `implementation-plan.md` Phase 6 §items win).
> **Zero owner-input steps this phase** — no datasets, no creds, no external
> downloads; everything runs autonomously once approved (defaults proposed
> below; approval = defaults locked, the Phase-5 "don't get blocked" pattern).
> Grounding facts verified in code 2026-08-29 (survey below).

## Objective

Single deterministic path from raw signals to operator events: the per-camera
candidate rows that `CameraAnalytics.process()` emits today become
**debounced, severity-mapped, schema-valid CloudEvents v0 envelopes** with
`model_versions` stamped and evidence refs on every visual kind. The engine is
**pure logic** (signal sequences in → event list out) and carries the
highest-value test suite in the repo: golden files.

**Gate 6:** golden-file tests pass (no duplicates, correct severities,
debounce windows honored) · engine is pure logic (no camera/network/DB calls
inside) · all outputs validate against `schemas/events/v0/`.

## Grounding facts (2026-08-29, verified in code)

| Item | Fact (where) | Consequence for us |
|---|---|---|
| Candidate rows today | `events/sink.py` `EventRow` — kinds emitted: `occupancy_level_change`, `restricted_zone_entry`, `door_obstruction`, `fall_detected`, `altercation_suspected` (5 of the 7 v0 enum kinds; `overcrowding` + `person_down` are reserved, nothing emits them) | Row→payload mapping covers 5 kinds; `overcrowding` becomes reachable via the band mapping below; `person_down` stays reserved |
| Rows are not envelopes | rows carry float epoch `ts`, kind-specific extras; `confidence` only on fall/fight; `evidence_ref` only on fall; no id/severity/location/model_versions | Normalizer must fill: ISO `timestamp`, `severity`, `confidence` defaults, `tracks[]`, `location`, `evidence_ref` (fight: Step 6.3b), `model_versions` |
| Envelope contract | `envelope.schema.json`: `specversion 1.0`, `id` (UUID, dedupe key), `source` `/mobisentra/…`, `type` `org.mobisentra.event.*`, `time` ISO, `datacontenttype application/json`; `event.schema.json`: required `event_type/severity/camera_id/timestamp/confidence` | Builder is a pure function row→envelope; `source` derives from `vehicle_id` + camera id (both in `cameras.yaml` registry) |
| Validation harness exists | `tests/test_schemas.py` already runs `jsonschema.Draft7Validator` against the shared schemas + example | Reuse the pattern; every engine output gets round-trip validated in tests |
| Wiring point | `pipeline.py`: `run_frame` → `analytics.process()` rows → `event_sink.write()`; `attach_analytics` composes `CameraAnalytics` + `JsonlEventWriter` | Phase 6 slots a per-camera `EventEngine` between `process()` and the sinks; the raw candidate JSONL stays (debug value), envelopes add a second sink |
| Evidence writer is fall-only | `evidence.py` `write_fall_clip`; `engine.py` docstring: "pair-clip generalization rides Phase 6" | Step 6.3b generalizes the writer so `altercation_suspected` rows carry `evidence_ref` too |
| Upstream already debounces some kinds | `OccupancyMonitor` confirm-frames + first-band-silent; `FallDetector`/`FightDetector` one-fire-per-engagement with re-arm | Engine debounce must not double-suppress: cooldown keys are (camera, kind, subject) with re-arm, and de-escalation rows are never suppressed by an escalation cooldown |
| Determinism for golden files | nothing stamps `model_versions`; `ts` is float epoch | Injectable clock + id-factory from day one (golden files cannot freeze wall-clock); `model_versions` derived from loaded artifacts at composition time |
| Severity policy (implementation-plan §Phase 6) | LOW (restricted zone) / MEDIUM (overcrowding) / HIGH (fall, aggressive) / CRITICAL (confirmed altercation, trapped) | Defaults below implement exactly this; "confirmed" = re-fire escalation proxy (no confirmation mechanism exists pre-backend) |

## Steps (summary — runbook 6.1–6.4)

| Step | Plan | Done when |
|---|---|---|
| 6.1 event engine service | `events/engine.py` — pure logic: normalize rows → v0 payloads, debounce (cooldown per camera+kind+subject, suppression counting, re-arm), severity via injected mapper | engine testable as a function: signal sequences in → event list out |
| 6.2 severity mapping | `configs/severity.yaml` — kind defaults + escalation rules (band ratio, action score, re-fire); thresholds in config, not code | editing YAML changes severity with zero code changes |
| 6.3 CloudEvents output + evidence generalization | envelope builder (id/source/type/time), `model_versions` stamped from loaded artifacts, fight evidence clips (writer generalization), wired into the run loop | all outputs validate against `schemas/events/v0/`; fight rows carry `evidence_ref` |
| 6.4 golden-file tests | scripted signal sequences → exact expected envelope streams | golden suite green = Gate 6 |

## Execution division (draft for approval — one-by-one, each independently verifiable)

**Dependency / blocking map:**

```
6.1a (payload normalizer + envelope builder, pure) ──┐
6.1b (debounce core, pure; stub severity)  ──────────┼→ 6.3a (wire into run loop:
6.2  (severity.yaml + loader + rules, pure) ─────────┘   envelopes sink + model_versions)
6.3b (evidence writer generalization — independent of 6.1/6.2)
6.4  (golden files + purity guard — consumes all)
```

Proposed execution order: **6.1a → 6.1b → 6.2 → 6.3a → 6.3b → 6.4**
(6.3b may swap with 6.3a — no dependency either way).

### 6.1a — Payload normalizer + envelope builder (pure; no owner input) ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| `EventPayload` normalizer: `EventRow` → v0 `data` dict (event_type, severity-from-mapper, camera_id, location=zone-or-null, tracks=[track]/[a,b], confidence (row value or rule-kind default 1.0), ISO timestamp from epoch ts, evidence_ref, model_versions) + `CloudEvent` envelope (specversion, uuid4 id via injectable id-factory, `/mobisentra/edge/<vehicle_id>/<camera_id>` source, `org.mobisentra.event.<kind>` type, time, datacontenttype). Injectable clock + id-factory (golden determinism) | `edge/mobisentra/events/envelope.py` + `edge/tests/test_envelope.py` | ✅ **27/27 tests green** (suite 235→262, ruff clean): all 5 emitted kinds → payload + envelope validate against the shared Draft-07 schemas; determinism proof (fixed id-factory → byte-identical); rejection tests (unknown kind, non-enum severity, out-of-range confidence, missing kind/camera_id/ts, bad source); enum drift-guard (code constants == schema enums). **Design notes:** (1) *clock injection dropped* — envelope `time` is the CloudEvents *occurrence* time derived from the row's stream `ts` (never wall-clock; replay-safe for 6× realtime benchmarks), so the only nondeterminism is `id` → id-factory injection suffices; (2) `event_type` override param added for the 6.2 into-`over` → `overcrowding` mapping (tested); (3) diagnostics allowlist (from_band/to_band/count/ratio/dwell/door_state/trigger_ts/action_score) passes through for dashboards — schema `additionalProperties: true`; (4) fail-fast everywhere, no clamping (out-of-range confidence raises) |

### 6.1b — Debounce core (pure; consumes 6.1a) ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| `EventEngine`: rows in → (emitted, suppressed) out. Cooldown = max 1 alert per X min per **(camera, kind, subject)** where subject = track_id (fall), pair (fight), zone (occupancy/dwell); falls back to (camera, kind) when no subject. De-escalation rows (band improving) never blocked by an escalation cooldown. Suppressed rows counted per key (telemetry, testable). Re-fire of the same fight subject within the escalation window bumps severity (the "confirmed" proxy) | `edge/mobisentra/events/engine.py` + `edge/tests/test_event_engine.py` | ✅ **17/17 tests green** (suite 262→279, ruff clean): every plan done-when case — repeated fall same track → 1 emit + N suppressed; different tracks → both emit; cooldown-expiry re-arm; occupancy flicker → transitions emitted, no duplicate transitions; de-escalation exempt even immediately after escalation; de-escalation re-arms the escalation key; fight Δt≤cooldown → suppressed / cooldown<Δt≤window → CRITICAL / Δt>window → fresh HIGH; pair order-proof keys; dwell keyed (zone, track) — second person = second incident; unknown kind → fail fast; reset(); all emitted envelopes schema-valid; determinism; **purity guard delivered early** (AST import-allowlist on engine.py — Gate-6 criterion enforced from day one). **Design notes:** (1) *occupancy family key* — escalation (kind `overcrowding` after the 6.2 override) and de-escalation (kind `occupancy_level_change`) resolve to different kinds, so both share one throttle slot per zone (kind-keyed state would never pair — caught by the flicker test, fixed in-engine); (2) dwell subject granularity refined from the plan's "zone" to **(zone, track)** — a second person entering is a second incident, consistent with the fall rule; (3) `last_fire` updates on emission only — suppression never extends a window (no sliding-throttle surprise); (4) severity/kind mapping injected as `ResolutionResolver` (stub in tests; 6.2 builds it from severity.yaml); (5) all windows measured on stream `ts`, never wall-clock |

### 6.2 — Severity config (pure; consumed by 6.1b tests + 6.3a wiring) ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| `configs/severity.yaml`: per-kind default severity + escalation rules (occupancy: into-`over` band → `overcrowding` kind, MEDIUM, HIGH if ratio ≥ escalates_at; other band flips → `occupancy_level_change` LOW. fight: HIGH default, CRITICAL on re-fire (6.1b). fall HIGH. restricted LOW. door MEDIUM) + per-kind cooldown minutes. Loader with strict validation (unknown kind/enum → fail fast at startup, never mid-stream) | `edge/configs/severity.yaml`, `edge/mobisentra/events/severity.py` + `edge/tests/test_severity.py` | ✅ **27/27 tests green** (suite 279→306, ruff clean). **YAML-edit proof:** file round-trip through the same loader — fall HIGH→LOW and ratio threshold 1.5→1.2 change resolver output with zero code changes; removing the escalation block disables it. Strict validation: malformed YAML, non-enum severity, kind typo (valid list in error), reserved `person_down` (distinct message), missing kind, non-positive cooldown, bad fight severity, unexpected top-level key — all `SeverityConfigError` with path + specifics at load. Resolver rules: into-`overcrowded` → `overcrowding` MEDIUM / HIGH at ratio ≥ 1.5 (missing ratio → base); other flips → `occupancy_level_change` LOW; per-kind pass-through; fail-fast on reserved/unknown kinds at resolve time. Composition test: real YAML policy → `EventEngine` end-to-end (fall HIGH, ratio-1.7 overcrowding HIGH, de-escalation LOW). Defaults = the approved table, locked by test. Design notes: (1) rule *structure* in code, *values* in YAML (the approved split); (2) minutes in YAML → seconds at load (single conversion point); (3) `EMITTABLE_KINDS = EVENT_TYPES − person_down` with drift-guard test; (4) escalation block is consumed from the `severity:` mapping before kind-key validation (initial bug: the nested block read as an unknown kind — caught by tests on first run) |

### 6.3a — Run-loop wiring + model_versions (consumes 6.1a + 6.1b + 6.2)

| Do | Files | Done when |
|---|---|---|
| `attach_analytics`/`run_frame`: per-camera `EventEngine` between `analytics.process()` and sinks; envelope JSONL sink at `runs/events/<cam>.envelopes.jsonl` (raw candidate log stays). `model_versions` assembled at composition from loaded artifacts (detector/pose model names from detection config, action onnx name + short SHA) and stamped into every envelope | `edge/mobisentra/pipeline.py`, `edge/mobisentra/events/sink.py` (envelope writer) + wiring tests | `--detect` run on sample footage → envelopes land in the new JSONL, **every line validates** against envelope+event schemas (validator over the file in the test); zero behavior change to raw rows |

### 6.3b — Evidence writer generalization (independent)

| Do | Files | Done when |
|---|---|---|
| Generalize `write_fall_clip` → `write_clip(kind=…)` (naming stem per kind, same MP4 + sidecar path); `CameraAnalytics._fight_row` snapshots the evidence buffer on fire and stamps `evidence_ref` (the documented Phase-6 deferral) | `edge/mobisentra/events/evidence.py`, `edge/mobisentra/analytics/engine.py` + tests | unit: fight fire with writer → clip + sidecar + `evidence_ref` set; fall path byte-identical behavior (existing tests stay green); retention applies to both |

### 6.4 — Golden files + purity guard (Gate 6)

| Do | Files | Done when |
|---|---|---|
| Scripted signal sequences → exact expected envelope streams as committed JSONL goldens: (1) repeated falls one track → one alert; (2) occupancy flicker → no duplicate/spurious events; (3) fight below fusion → nothing (upstream silence preserved through the engine); (4) full mixed scenario (fall + fight re-fire escalation + zone + occupancy bands + cooldown expiry re-arm). Purity guard: AST import-allowlist test on `events/engine.py` (stdlib + typing only — no cv2/network/DB) | `edge/tests/golden/*.json` + `edge/tests/golden/README.md` + `edge/tests/test_golden.py` + purity test in `test_event_engine.py` | golden suite green byte-exact (fixed clock/id-factory); purity test fails if engine imports anything outside the allowlist |

## Gate 6 — checklist (from the runbook)

- [ ] Golden-file tests pass — no duplicates, correct severities, debounce windows honored
- [ ] Engine is pure logic (no camera, network, or DB calls inside) — enforced by the purity guard test
- [ ] All events validate against schema v0 — validator over emitted files + goldens

## Proposed defaults (approve or edit — approval locks them)

| Item | Default | Why |
|---|---|---|
| Severity: `restricted_zone_entry` | LOW | plan §Phase 6 |
| `overcrowding` (into-`over` band) | MEDIUM; HIGH when ratio ≥ 1.5 | plan: MEDIUM; escalation knob per operator |
| `occupancy_level_change` (non-over flips, incl. de-escalation) | LOW | informative resolution, never CRITICAL/HIGH |
| `fall_detected` | HIGH | plan §Phase 6 |
| `altercation_suspected` | HIGH; **CRITICAL on re-fire** of the same pair within the escalation window | "aggressive" = HIGH, "confirmed altercation" = CRITICAL — re-fire is the only pre-backend confirmation proxy |
| `door_obstruction` | MEDIUM | "trapped" CRITICAL needs a detector we don't have; escalation is a Phase 10 tuning knob |
| Cooldowns (minutes) | fall 5/track · fight 3/pair (escalation window 10) · occupancy 2/zone · restricted 10/track · door 5/zone | sized from dwell/occupancy cadences; all in severity.yaml |
| `confidence` for rule-based kinds | 1.0 (deterministic count/dwell rules) | schema requires the field; fabricating scores would be worse |
| `location` | zone name when the row has one, else null | matches the fall_envelope example shape |
| Envelope sink | `runs/events/<cam>.envelopes.jsonl` beside the raw candidates | raw log keeps debug value; Phase 7 publisher consumes the same engine stream |
| `model_versions` | derived from loaded artifacts at composition (e.g. detector/pose model names, action onnx `name@sha8`) | config would drift from what actually runs |

## Risks

| Risk | Mitigation |
|---|---|
| Double-debouncing (upstream confirm-frames + engine cooldown) suppresses resolution events | cooldown keyed per (kind, subject); de-escalation rows explicitly exempt (6.1b tests cover exactly this) |
| Golden files accidentally freeze wall-clock/uuids | injectable clock + id-factory from 6.1a day one; golden determinism test in 6.1a |
| Severity drift between edge config and backend expectations | severity stays enum-constrained by the shared schema; defaults + rules recorded here; backend (Phase 8) consumes, never re-derives |
| Purity regression (engine grows an I/O import) | AST allowlist test fails CI before review sees it |
| Fight pair crops tiny/off-frame → useless clips | writer reuses the validated fall pipeline; buffer window may be short — golden/unit tests assert a clip exists, not its content quality (quality rides Phase 10) |

## Open questions — resolve at approval

1. **Severity + cooldown defaults table above** — approve as-is or edit values (YAML makes later changes cheap; these are just the shipped defaults).
2. **`overcrowding` vs `occupancy_level_change` mapping** — proposed: entering `over` emits `overcrowding` (uses the reserved enum kind); all other flips (incl. leaving `over`) emit `occupancy_level_change` LOW. Alternative: single kind with severity varying — rejected because the enum carries both and dashboards will filter on `overcrowding`.
3. **Envelope sink naming** — proposed `<cam>.envelopes.jsonl`; alternative is replacing the raw log (rejected: raw candidates are the debugging ground truth).
