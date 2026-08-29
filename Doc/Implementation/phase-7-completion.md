# Phase 7 — Completion Report

> **Status: EXECUTED + CLOSED (2026-08-29) · Gate 7: 3/3 ticked — PASSED via the one-shot soak (run `4ed37729`)**
> Scope executed: `Doc/Implementation/phase-7-plan.md` (approved 2026-08-29; steps 7.1a–7.4b run one-by-one per the working agreement; no owner-input steps)
> Runbook: `implementation-sequence.md` → Phase 7

---

## 1. Gate 7 — Evidence

| # | Criterion | Result |
|---|---|---|
| 1 | 10-minute network blackout during active events → all events arrive post-reconnect | ✅ **PASS** — soak `runs/messaging-soak.json` (run `4ed37729`, ≈13 min): 120 events published during the 10-min EMQX blackout (zero wire leakage, all spooled at the edge), full backlog replayed after reconnect — 144/144 sent events in Kafka |
| 2 | Zero loss, zero duplicates after dedupe | ✅ **PASS** — lost=0, unexpected=0, spool dropped=0; 6 wire-level redeliveries (QoS-1 at-least-once, honestly expected around the broker restart) collapsed to **0 duplicates** by CloudEvents-id dedupe |
| 3 | Broker kill/restore mid-stream: no crash, full replay | ✅ **PASS** — Kafka killed during active publishing; bridge backpressure engaged (persistent-session MQTT disconnect + bounded buffer), watermark-probe recovered automatically; the whole stack healthy post-run; 0 loss through the epoch |

## 2. What Was Built

| Piece | Role |
|---|---|
| `edge/mobisentra/messaging/spool.py` | SQLite write-ahead queue (WAL, runbook schema): FIFO by rowid, **id-dedupe that survives sends** (at-least-once redeliveries can never re-queue), retention cap with persisted drop counter, thread-safe, crash-safe reopen |
| `edge/mobisentra/messaging/publisher.py` | `EventPublisher` — `publish()` is the only pipeline-facing call (fast local write, never blocks on network); `drain_once` = pending FIFO → PUBACK-synced deliver → batched mark_sent; exponential backoff 1→60 s; injectable transport |
| `messaging/config.py` + `configs/messaging.yaml` | strict loader (exact keys, scheme allowlist, positive numbers); **the Phase-0 dotted-topic gotcha is a hard config error**; approved defaults locked by test |
| `messaging/transport_paho.py` | paho v2 adapter: lazy connect (broker-down-at-startup = blackout from t=0), PUBACK-synced `deliver` with timeout, auto-reconnect — live-proven against EMQX |
| `pipeline.py` + `main.py` | `attach_messaging` (ONE edge-wide publisher), `MessagingHandle.shutdown()` (stop → final drain → close), `--publish` default OFF, `--messaging-config`; CLI guards verified live |
| `bridge/` hardening | `lib/` (topics, suppressor, bounded buffer) + vitest 15/15; **lossless backpressure** = MQTT disconnect with persistent session (EMQX queues + flow-controls) + bounded buffer with counted drops; watermark-query recovery probe; counters log; env docs |
| Soak + tools | `tools/messaging_check.py` (round-trip verdict), `tools/messaging_soak.py` (phased fault epochs + JSON evidence) |
| Tests | edge suite **235 (Phase-5 close) → 369 passed + 4 skipped**; bridge 15/15 + tsc clean |

## 3. Verification

- Unit/CI-able: spool 11, publisher 11, wiring 13, resilience 5 (fault injection in 0.06 s, no broker), integration gated (skips cleanly when the stack is down; skip-gate itself tested)
- Live: 100-envelope test round trip byte-intact + ordered; CLI check 50/50; bridge kill rehearsals (incl. automatic recovery, 10/10 exact ids); the Gate-7 soak above
- Full-stack evidence: `runs/messaging-soak.json` (+ rehearsal JSON alongside)

## 4. Process Notes (honest)

- **Two bridge flaws found live and fixed** (7.3b): the down-detection never matched this librdkafka failure mode (→ text+code matching; en route we verified librdkafka's internal retransmit already covers short outages — layered safety), and buffer-gated recovery deadlocked in the disconnect epoch (→ watermark-query probe; invariant comment-documented).
- **The soak rehearsal earned its keep**: it exposed the EMQX-restart convergence latency (~40–90 s of paho reconnect backoff + broker boot) — the system self-recovers with zero loss; the gate run's recovery window absorbed it.
- The soak's 6 wire duplicates are the at-least-once contract made visible — the reason Phase-8 consumer dedupe exists; the gate's "zero duplicates **after** dedupe" is exactly what was measured.
- Backpressure design deviated from the plan's "pause MQTT" (mqtt.js v5 has no pause; unsubscribing is lossy) — replaced by persistent-session disconnect, strictly stronger. Recorded in the plan.
- Owner's long-run directive honored: the 10-min blackout ran exactly once, as an explicit one-shot tool with live progress lines, never in CI.

## 5. Verification Commands

```bash
cd edge
.venv/bin/python -m pytest                    # 369 passed + 4 skipped (2026-08-29)
.venv/bin/python -m ruff check .              # clean
cd ../bridge && pnpm test && pnpm typecheck   # 15/15 + tsc clean
# gate evidence (one-shot, ≈13 min):
cd ../edge && .venv/bin/python tools/messaging_soak.py --blackout-min 10 --recovery-s 90
```

## 6. Next

1. **Phase 8 — Backend Services** (Node/TS: Kafka consumer with id-dedupe, PostgreSQL history, Redis live state, Socket.IO push). The envelope stream in Kafka `mobisentra.events` is its input, ready as-is.
2. Parked owner decisions (unchanged): fight production enablement, Phase 2 A′ waive, Phase 3 occupancy counts, Phase 4 FP closure.
