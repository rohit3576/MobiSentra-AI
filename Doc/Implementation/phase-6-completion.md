# Phase 6 — Completion Report

> **Status: EXECUTED + CLOSED (2026-08-29) · Gate 6: 3/3 ticked — PASSED on first evaluation**
> Scope executed: `Doc/Implementation/phase-6-plan.md` (approved 2026-08-29; steps 6.1a–6.4 run one-by-one per the working agreement; zero owner-input steps as planned)
> Runbook: `implementation-sequence.md` → Phase 6

---

## 1. Gate 6 — Evidence

| # | Criterion | Result |
|---|---|---|
| 1 | Golden-file tests pass — no duplicates, correct severities, debounce windows honored | ✅ **PASS** — 4 goldens (`tests/golden/`): repeated-falls (1 alert/track/cooldown + suppression counting + re-arm), occupancy-flicker (duplicate suppression, de-escalation exempt + re-arm, family key, ratio→HIGH), fight-below-fusion (upstream silence preserved), mixed-scenario (7 envelopes: multi-row frames, order-proof pair key, fight re-fire Δ480 s → CRITICAL, fall re-arm, no cross-kind interference). All byte-exact vs the real resolver+engine; **manually reviewed line by line**; a deliberate mutation (one severity flip) failed the suite, proving sensitivity |
| 2 | Engine is pure logic (no camera/network/DB inside) | ✅ **PASS** — AST import-allowlist guard on `events/engine.py` (shipped with 6.1b, runs in every suite); extending the allowlist is a deliberate, reviewable act |
| 3 | All events validate against schema v0 | ✅ **PASS** — validated over: goldens, wiring tests, and the 6.3a production smoke (real stack: yolo26n-pose/MPS, bus1.mp4 → 60 rows → 55 envelopes, 5 debounce-suppressed, **55/55 Draft-07-valid**; kinds seen: occupancy_level_change LOW, overcrowding HIGH at ratio ≥ 1.5, restricted_zone_entry LOW) |

## 2. What Was Built

| Module | Role |
|---|---|
| `mobisentra/events/envelope.py` | `to_payload` (rows → v0 data: tracks/zone→location/confidence defaults/ISO-Z timestamps/diagnostics allowlist) + `EnvelopeBuilder` (per-camera CloudEvents 1.0, injectable id-factory, fail-fast, enum drift-guard vs schemas) |
| `mobisentra/events/engine.py` | pure debounce core: cooldown per (camera, kind, subject), occupancy **family key** (escalation arms / de-escalation re-arms one slot per zone), fight re-fire → CRITICAL within the escalation window, suppression telemetry, stream-`ts` time base (replay-safe) |
| `mobisentra/events/severity.py` + `configs/severity.yaml` | strict YAML loader (fails at startup, never mid-stream) + resolver (into-`overcrowded` → `overcrowding` kind, ratio escalation) + debounce bridge; operator edits need zero code changes (YAML-edit proof by test) |
| `mobisentra/pipeline.py` + `main.py` | per-camera EventEngine wired between `CameraAnalytics.process()` and sinks; envelopes → `runs/events/<cam>.envelopes.jsonl` (raw candidate log stays); `build_model_versions` stamps what actually runs; `--severity-config` flag |
| `events/evidence.py` + `analytics/engine.py` | writer generalized: fight pair clips (`fight_track4-track11_t*.mp4` + sidecar) with `evidence_ref` stamped on `altercation_suspected`; fall path byte-identical; retention spans kinds |
| `tests/golden/` + `test_golden.py` | golden-master suite with `GOLDEN_REGEN=1` maintenance path (README documents the review-the-diff rule) |
| Tests | suite grew **235 → 327 passed + 4 skipped** across the phase (envelope 27, engine 17, severity 27, wiring 9, evidence+fight 8, goldens 4, +31 pre-existing since Phase 5 close); ruff clean throughout |

## 3. E2E Verification

- Production smoke (6.3a): `python -m mobisentra.main --detect` on sample footage → envelope JSONL validates line-by-line; debounce visibly suppressed 5 of 60 rows.
- Fight evidence (6.3b): full-stack stub-scorer run → playable MP4 (cv2 re-open, all frames), sidecar `kind=fight`, writer-less composition stays evidence-free.
- Goldens (6.4): byte-exact against the real resolver+engine; regeneration reviewed.

## 4. Process Notes (honest)

- **Two in-loop bugs caught by tests, fixed before shipping:** (1) kind-keyed occupancy state meant de-escalation never re-armed the escalation key → family-key fix (6.1b); (2) the YAML escalation block read as an unknown kind → loader consumes it before kind validation (6.2). Neither reached a commit.
- **Fight production enablement remains open (owner decision):** the action scorer factory is still benchmark/soak-only — production envelopes stamp `detector` only; `build_model_versions(..., action_onnx)` ships ready with `name@sha8` stamping. One flag in `attach_analytics` when the owner wants live fight events.
- **Golden regen discipline:** 6.4's mutation check proved sensitivity; a restore mistake during that check corrupted one golden's expected block — regenerated from the verified engine and re-reviewed (the regen path working as designed, and a reminder to use targeted edits over sed).
- Time semantics are stream-`ts` everywhere (never wall-clock) — 6× realtime benchmark replays get correct debounce behavior for free.

## 5. Verification Commands

```bash
cd edge
.venv/bin/python -m pytest                      # 327 passed + 4 skipped (2026-08-29)
.venv/bin/python -m ruff check .                # clean
.venv/bin/python -m pytest tests/test_golden.py # Gate 6 criterion 1
# Production smoke (events need an event-friendly registry or bus1.mp4):
.venv/bin/python -m mobisentra.main --config configs/cameras.yaml --detect --minutes 0.5
```

## 6. Next

1. **Phase 7 — Edge Messaging** (MQTT QoS 1 + SQLite spool → bridge → Kafka; zero-loss gates). The engine's envelope stream is the publisher's input, ready as-is.
2. Owner decision (parked): enable the fight path in production wiring (`action` version stamp + live `altercation_suspected` envelopes).
3. Deferred tracker (unchanged): Phase 2 A′ waive, Phase 3 occupancy manual counts, Phase 4 FP closure → `phase-4-completion.md`.
