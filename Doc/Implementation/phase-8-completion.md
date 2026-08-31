# Phase 8 — Completion Report

> **Status: EXECUTED + CLOSED (2026-08-31) · Gate 8: 3/3 ticked — PASSED (evidence below)**
> Scope executed: `Doc/Implementation/phase-8-plan.md` (approved 2026-08-29; steps run one-by-one per the working agreement — order 8.1b → 8.1a → 8.2 → 8.3a → 8.3c → 8.3b → 8.4a → 8.5 → 8.4b, with the phase-9-planned amendments A1/A2 folded into 8.3b/8.4a before they were built)
> Runbook: `implementation-sequence.md` → Phase 8

---

## 1. Gate 8 — Evidence

| # | Criterion | Result |
|---|---|---|
| 1 | Consumer restart mid-stream: zero lost/duplicated rows | ✅ **PASS** — integration suite: 150-event burst, consumer **SIGKILLed with 5–27 rows landed** → revived process redelivers uncommitted offsets → final **150 rows / 150 distinct, exact id-set match, zero double-push** (Redis NX dedupe + PG `ON CONFLICT` collapsing every redelivery; one WS push in flight at the kill instant — receipts 149/150, by design) |
| 2 | Dashboard-facing client receives an event < 1 s after edge publish | ✅ **PASS** — worst publish→WS-receipt **23 ms** (probe runs 19–28 ms; ≈40× headroom); full-edge-path round-trip (publisher→EMQX→bridge→Kafka→consumer→PG) 11.5 s for 3 events incl. the tool's own consumer window |
| 3 | Ack/escalate write audit rows | ✅ **PASS** — asserted over live HTTP: POST ack + escalate → **two `audit_log` rows** (`ack`, `escalate`, actor recorded) + `acked_at`/`acked_by`/`escalation.actor` columns verified; actions are UPDATE+audit in ONE transaction (no orphan audit rows — 0-row update rolls back) |

## 2. What Was Built

| Piece | Role |
|---|---|
| `consumer/kafka.ts` | `EventConsumer` (manual offset commits **only after the whole batch resolves**, injectable driver) + `LibrdkafkaDriver` (flow-mode, per-partition max+1 commits, commit-error routing, 6 s session timeout) |
| `lib/events.ts` + `schema/events.ts` | ajv-first envelope→`EventRecord` mapping with runtime-guarded extraction (zero casts); analytics classification + `toAnalyticsState` (A1 contract path); occupancy extraction from safety events (A1 live path) |
| `consumer/dedupe.ts` | `SET dedupe:{source}:{id} NX EX 86400` — **fails OPEN** on Redis outage (PG is the never-blinking net) |
| `schema/migrations/` + `migrate.ts` | `events` + `audit_log` DDL (severity CHECK, FK, filter indexes) + ~40-line ordered-SQL runner (`schema_migrations`, refuses divergent history loudly) |
| `ws/push.ts` | Socket.IO: `alerts:{vehicle}` + `cameras:{vehicle}` rooms, ack'd+guarded `subscribe`, `EventPusher` boundary |
| `consumer/pipeline.ts` | Write path: classify → dedupe → PG `ON CONFLICT` → Redis live state (`camera:{id}`, TTL 300) → WS push; analytics branch (Redis-only, no PG row); **PG-fails→throw, Redis-fails→degrade, invalid→skip** |
| `api/` | Fastify: 6 endpoints + A2 evidence route (`EVIDENCE_ROOT`, traversal-sandbox, Range/206 for Safari `<video>`); actions transactional with audit |
| `index.ts` + Dockerfile + compose svc | One-process assembly (boot migrations, consumer supervisor w/ backoff, graceful SIGTERM); compose service `backend` (port 3000, schema baked into the image, root-context build w/ allowlist `.dockerignore`) |
| Tests | **87 passed** (unit + gated live-stack integration; 0 skipped with the stack up) |

## 3. Verification

- Unit (no stack): 83 tests across 9 files; `tsc --noEmit` clean; adapters' SQL/args asserted query-by-query
- Integration (`test/integration/`): 4 tests vs the live compose stack — three-store landing, latency probe, HTTP ack/escalate→audit, crash-restart zero-loss/zero-dup; **gated skip proven** (kafka stopped → clean skip + instructions); warmup-receipt readiness gate absorbs group-rebalance latency
- Full-edge-path: `tools/messaging_check.py` PASS ×2 (runs `1144d0c5`, `43fe2458` — 3/3 each, rows verified in PG)
- 8.4b done-when: `docker compose up -d --build backend` → containerized consumer processing the real edge path, host API 200 with live camera status — **zero host-side tooling**
- Live smokes along the way: migrations idempotent apply; PG conflict net (dedupe bypassed → `conflict`); escalate 42P18 caught+fixed; evidence 200/206/403

## 4. Process Notes (honest)

- **The `.d.ts` lied, the source didn't**: kafka-javascript's callback-only `consume()` actually delivers SINGLE messages and forwards idle timeouts (code −185) as errors — the 8.1b driver (fake-tested only) crashed on first live contact. The "live-tested in 8.5" note in the code was the debt this step paid. Lesson repeated: **runtime guards over typed signatures at every native-lib boundary.**
- **Orphaned consumers are invisible saboteurs**: `pnpm exec` doesn't forward signals, so every early harness kill/stop leaked a live consumer that silently owned the group partition — PG kept filling while WS receipts read zero. Diagnosed via `kafka-consumer-groups --describe` (a member existed before spawn); fixed with process-group signals (`detached` + `kill(-pid)`); "no orphans after runs" is now a standing check.
- **Fresh group + `earliest` replays the topic backlog by design** — first boot ingested 1,162 Phase-7 history events into dev PG (wire dupes collapsed live: visible `duplicate` outcomes). Correct durable-catch-up behavior, recorded so it never surprises anyone.
- Flaky-looking failures were all harness-side: rebalance-latency (fixed by warmup gate + 6 s session timeout) and a self-inflicted kafka restart (gated-skip proof) — never backend logic.
- 8.4b caught the missing-shared-schemas crash only in the container (`../../../schemas` escapes `/app`) — the frozen contract is now baked into the image at build time.

## 5. Handoff to Phase 9

Backend surface for the dashboard: REST on :3000 (see `backend/README.md`), Socket.IO same port (`subscribe` + ack → `alerts:`/`cameras:` rooms), evidence under `/api/evidence/*` with `EVIDENCE_ROOT` (compose binds `edge/runs/evidence` read-only). Phase-9 plan approved 2026-08-30 (auth dropped; level-display-not-recompute; demo-replay compose profile). Caution: run the backend either on the host OR as a compose service — one consumer holds the group's partition.
