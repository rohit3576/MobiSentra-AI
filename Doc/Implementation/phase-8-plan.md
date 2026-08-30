# Phase 8 — Backend Services · Plan

> **Status: APPROVED 2026-08-29 (owner: "lets start the plan … small small
> divide"; execution began at 8.1b per owner directive — valid: 8.1a and
> 8.1b are independent branches in the dependency map).**
> Source of truth: `implementation-sequence.md` Phase 8 (on conflict, the
> runbook + `implementation-plan.md` §2 win).
> **No owner-input steps** — dev creds are the compose defaults; the stack
> is already up and `mobisentra.events` is flowing schema-valid
> CloudEvents (Phase 7's live evidence). `tools/messaging_check.py`
> generates test traffic on demand.
> Grounding facts verified in code 2026-08-29 (survey below).

## Objective

Durable history + live state + real-time push: a Node/TS backend consumes
`mobisentra.events`, lands every event in PostgreSQL exactly once
(at-least-once delivery × id-dedupe × `ON CONFLICT` — the Phase-7 soak
proved wire duplicates are real, so the dedupe is load-bearing), maintains
Redis live state per camera, pushes over Socket.IO per-vehicle rooms, and
serves the REST API the Phase-9 dashboard needs.

**Gate 8:** consumer restart mid-stream → zero lost/duplicated PG rows ·
dashboard-facing client receives an event < 1 s after edge publish ·
ack/escalate write audit rows.

## Grounding facts (2026-08-29, verified in code)

| Item | Fact (where) | Consequence for us |
|---|---|---|
| Backend skeleton exists | `backend/` — pnpm package, strict tsc, vitest, `src/{api,consumer,ws,schema}` (three are `.gitkeep`); `ajv` dep | Only `schema/events.ts` is real (envelope validation vs shared schemas, RFC-3339 format). Everything else is Phase-8 work |
| Shared-schema contract | `backend/src/schema/events.ts` (Phase 0): ajv validators for envelope+event | The consumer validates before processing — contract freeze already tested edge↔backend |
| Topic reality | ONE canonical topic `mobisentra.events` (bridge maps `mobisentra/#` → dots; only `/events` is used) | Runbook 8.1 says "all four topics" — **stale**; we subscribe to the single canonical topic (deviation recorded) |
| Stores in compose | postgres:16 (`mobisentra:mobisentra@localhost:5432/mobisentra`), redis:7 (`localhost:6379`), kafka external `localhost:9092` — all healthy right now | No infra work; tests run against the live stack (gated like the edge integration test) |
| Kafka client | bridge already uses `@confluentinc/kafka-javascript` (v1.10, proven in anger through the Phase-7 kill rehearsals) | Same client + version in backend (runbook names it too); consumer API with manual offset commits |
| Envelope fields available | CloudEvents: `id` (dedupe), `source` (`/mobisentra/edge/{vehicle}/{camera}`), `type`, `time`; data: `event_type/severity/camera_id/timestamp/tracks/location/evidence_ref/model_versions` | vehicle_id parses from `source`; everything the API filters on is already in the payload |
| Bridge conventions | `bridge/`: strict TS (`verbatimModuleSyntax`, `noUncheckedIndexedAccess`), vitest, lib/ extraction + thin index | Backend mirrors the same conventions — one house style across TS services |

## Steps (summary — runbook 8.1–8.5)

| Step | Plan | Done when |
|---|---|---|
| 8.1 Kafka consumer | `@confluentinc/kafka-javascript` consumer, manual offset commits (commit only after successful processing), graceful shutdown | consumer drains `mobisentra.events` with clean SIGTERM handling |
| 8.2 Idempotency | Redis dedupe before processing: `SET dedupe:{source}:{id} NX EX 86400` — skip if already set | replayed events processed exactly once (unit + integration) |
| 8.3 Write path | PG insert `ON CONFLICT (event_id) DO NOTHING` → Redis live-state update (latest per camera, TTL) → Socket.IO room emit (`alerts:{vehicle_id}`) | one published edge event lands in all three stores; a test client receives it |
| 8.4 REST API | incidents list/detail, cameras + live status, events history, ack + escalate (audit-logged) | all endpoints integration-tested against the compose stack |
| 8.5 Integration tests | publish → consume → PG row + Redis state + WS message; consumer restart mid-stream | restart mid-stream → no lost/duplicated PG rows |

## Execution division (draft for approval — one-by-one, each independently verifiable)

**Dependency / blocking map:**

```
8.1a envelope→record mapping (pure lib, ajv) ──┐
8.1b kafka consumer wrapper (manual commits,   │→ 8.3b write-path pipeline ──→ 8.5 integration suite
      injectable processor, graceful stop)     │        (validate→dedupe→PG→Redis→WS)
8.2  redis dedupe (injectable client) ─────────┤              ↑
8.3a PG schema + tiny SQL migration runner ────┘        8.3c socket.io server (rooms)
8.4a REST API (fastify, 6 endpoints, audit) ── independent of consumer; consumed by 8.5
8.4b backend compose service (Dockerfile + svc) — last, for the Phase-9 demo path
```

Proposed execution order: **8.1a → 8.1b → 8.2 → 8.3a → 8.3c → 8.3b →
8.4a → 8.5 → 8.4b**.

### 8.1a — Envelope → record mapping (pure; no owner input) ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| `lib/events.ts`: schema-valid envelope → typed `EventRecord` (id, source, vehicle_id + camera_id parsed from `/mobisentra/edge/{v}/{c}`, event_type, severity, timestamp, tracks, location, evidence_ref, model_versions, raw JSON); reject invalid with structured errors (reuse `schema/events.ts` validators) | `backend/src/lib/events.ts` + `test/events.test.ts` | ✅ **14/14 tests green + tsc clean** (backend suite 26/26). The shared-schema example maps end-to-end (every field asserted incl. raw passthrough); one valid envelope per kind (6) round-trips; malformed → structured rejections (missing id, non-object, bad severity with the offending value, missing data.timestamp); optional fields degrade to null/[] /{} not errors; **source fallbacks proven: 4-segment source → camera from data, non-edge shape → vehicle `unknown`, bare prefix → unknown/unknown — never a crash**. Design notes: (1) validation = ajv (shared schemas, the Phase-0 freeze) THEN runtime-guarded extraction — zero casts, so schema/code drift fails loudly at the parse point instead of surfacing `undefined` downstream; (2) **camera precedence decided by the shared example itself**: `data.camera_id` (the edge registry key, what the API queries) wins over the source path segment (`BUS_102/CAM_04` vs `BUS_102_CAM_04` — they genuinely differ in the example); vehicle only exists in `source` → source-first with `unknown` fallback (comment-documented) |

### 8.1b — Kafka consumer wrapper (injectable processor) ✅ 2026-08-29 *(owner-directed first step)*

| Do | Files | Done when |
|---|---|---|
| `consumer/kafka.ts`: kafka-javascript consumer, group `mobisentra-backend`, subscribe `mobisentra.events`, **manual offset commit only after the processor resolves** (at-least-once, restart-safe), batch drain with bounded concurrency, graceful SIGTERM (stop fetching → finish in-flight → commit → disconnect); processor injected | `backend/src/consumer/kafka.ts` + `test/kafka-consumer.test.ts` (fake driver) | ✅ **6/6 unit tests green + tsc clean** (backend suite 12/12 incl. the Phase-0 schema tests). `EventConsumer` over a minimal `ConsumerDriver` protocol (the edge-publisher Transport pattern): **commit only after the whole batch resolves** (ordering proven: `fetch→process×N→commit`); processor failure → `run()` rejects with **zero commits and no further fetches** (no silent skips — supervisor restarts with backoff, restart replays safely); commit failure → visible rejection; graceful stop → in-flight batch finishes + commits + driver closed; empty fetches → neither process nor commit. Sequential processing = bounded concurrency 1 (per-partition order preserved; concurrency knob deferred — recorded). **Live findings:** (1) pnpm 11 `allowBuilds` gating blocked the native addon → workspace file now allows `@confluentinc/kafka-javascript` (bridge already did), 4m47s build; (2) the real librdkafka `commit()` is **synchronous fire-and-forget** — errors surface via the `offset.commit` event (verified from the shipped `.d.ts`, not assumed) → driver routes that event into the transport-error path; commit sends per-partition max offset + 1 (resume-from semantics); (3) a fake driver returning instantly-resolving promises **starves the event loop in microtasks** and hangs the stop-timers — the fake yields via `setTimeout 0` (comment-documented; caught as a real hang, killed the orphaned vitest) |

### 8.2 — Redis dedupe (injectable client) ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| `consumer/dedupe.ts`: `SET dedupe:{source}:{id} NX EX 86400` wrapper — returns first-seen true/false; injectable redis client (ioredis in prod, fake in tests) | `backend/src/consumer/dedupe.ts` + `test/dedupe.test.ts` | ✅ **5/5 tests green + tsc clean** (backend suite 31/31). First sighting processes, repeat skips, per-id granularity; TTL = runbook 86400 s (asserted) with custom override honored; key shape `dedupe:{source}:{id}` locked by test; adapter maps the real SET-NX-EX result (`OK` → first, `null` → duplicate) with `["k","1","EX",ttl,"NX"]` args asserted; **redis down → fails OPEN** (returns process + logs — PG `ON CONFLICT` is the never-blinking second net; drop-events-on-redis-blink documented as the wrong trade). ioredis added (pure JS, 2.3s install); type note: ioredis's class type imports as the NAMED export (`import type { Redis }`), not the default |

### 8.3a — PG schema + migrations

| Do | Files | Done when |
|---|---|---|
| `events` table (event_id PK, envelope JSONB, event_type, severity, camera_id, vehicle_id, occurred_at, received_at, acked_at, acked_by, escalation jsonb) + `audit_log` (action, event_id, actor, at, detail jsonb); ordered `.sql` files applied by a ~40-line runner (pg client + `schema_migrations` table; **no ORM, no migration framework** — SQL stays visible) | `backend/src/schema/migrations/*.sql`, `backend/src/schema/migrate.ts` + `test/migrate.test.ts` | unit (fake pg or temp schema): fresh DB → tables exist; rerun → no-op; missing file order → clear error; live apply against compose postgres verified in 8.5 |

### 8.3c — Socket.IO push server

| Do | Files | Done when |
|---|------|---|
| `ws/push.ts`: Socket.IO server, room per vehicle `alerts:{vehicle_id}`, typed push payload (the EventRecord), connection/disconnect logs; attach path configurable for Phase-9 CORS | `backend/src/ws/push.ts` + `test/push.test.ts` (in-memory socket.io server + client) | unit: two clients in different vehicle rooms → each gets only its room's events; payload shape asserted |

### 8.3b — Write-path pipeline (consumes 8.1a + 8.2 + 8.3a + 8.3c)

| Do | Files | Done when |
|---|---|---|
| `consumer/pipeline.ts`: validate → map → dedupe → `INSERT … ON CONFLICT (event_id) DO NOTHING` → Redis live state (`camera:{id}` hash: last event, severity, ts; TTL 5 min) → WS room emit; every store injectable; returns per-stage outcome for observability | `backend/src/consumer/pipeline.ts` + `test/pipeline.test.ts` | unit (all fakes): fresh event → PG insert + Redis update + WS emit; duplicate id → single PG row, no re-emit; PG down → error propagates (no commit → redelivery); Redis live-state failure → event still lands in PG (degrade, log) |

### 8.4a — REST API (fastify)

| Do | Files | Done when |
|---|---|---|
| GET `/api/incidents` (filters: severity, camera, vehicle, time window; newest-first), GET `/api/incidents/:id` (detail + evidence_ref), GET `/api/cameras` (distinct from PG + live status from Redis: online if state TTL alive), GET `/api/events` (raw history, cursor paging), POST `/api/incidents/:id/ack` + `/escalate` (write `events` ack columns **and** `audit_log` row); fastify + typed handlers, pg pool injected | `backend/src/api/server.ts` + `test/api.test.ts` (fastify inject + fake stores) | unit: all 6 endpoints happy-path + filters + 404s; ack/escalate → audit rows present; live against compose in 8.5 |

### 8.5 — Integration suite (compose stack; gated)

| Do | Files | Done when |
|---|---|---|
| Vitest suite against the live stack (gated on stack reachability, skip-with-instructions like the edge pattern): publish N envelopes straight to Kafka → assert PG rows + Redis state + WS client receipts; **latency probe** (publish→WS-receive < 1 s, the Gate-8 criterion); **restart mid-stream**: stop the consumer process mid-batch → restart → zero lost/duplicated PG rows (at-least-once redeliveries collapsed by dedupe + ON CONFLICT); one full-edge-path case via `tools/messaging_check.py` | `backend/test/integration/*.test.ts` (+ tiny TS test publisher) | all green with the stack up; gated skip proven; evidence numbers recorded in this plan |

### 8.4b — Backend compose service (last; demo path for Phase 9)

| Do | Files | Done when |
|---|---|---|
| `backend/Dockerfile` (pnpm, tsx runtime like bridge) + compose service (`backend`, depends_on pg/redis/kafka-healthy, env = host URLs); `backend/README.md` run/test docs | `backend/Dockerfile`, `infra/docker-compose.yml`, `backend/README.md` | `docker compose up backend` → consumer running against the stack, events flow end-to-end without host-side pnpm |

## Gate 8 — checklist (from the runbook)

- [ ] Consumer restart mid-stream: zero lost/duplicated rows
- [ ] Dashboard receives an event < 1 s after edge publish
- [ ] Ack/escalate write audit rows

## Proposed defaults (approve or edit — approval locks them)

| Item | Default | Why |
|---|---|---|
| Kafka topic / group | `mobisentra.events` / `mobisentra-backend` | one canonical topic (bridge contract); named group survives restarts |
| Offset semantics | manual commit **after** pipeline success (at-least-once) | runbook spec; duplicates collapsed by dedupe + ON CONFLICT — the proven-needed double net |
| Redis client | `ioredis` | mature, test-friendly; dev URL `redis://localhost:6379` |
| Dedupe key / TTL | `dedupe:{source}:{id}` / 86400 s | runbook spec verbatim |
| Live-state TTL | 300 s (`camera:{id}` hash) | camera shows offline when stale — matches operator expectations |
| PG client / migrations | `pg` + ~40-line ordered-SQL runner (`schema_migrations` table) | no ORM — SQL visible, parse-don't-validate at the boundary |
| HTTP framework | **Fastify** (+`@fastify/cors` for the dashboard origin) | TS-first, schema-friendly, fast; Express is the fallback if you prefer ubiquity |
| Data model | single `events` table doubling as incidents (ack columns + audit_log) | every envelope IS the incident record; dashboard filters by severity — no premature two-table split |
| WS rooms | `alerts:{vehicle_id}` | runbook spec; camera rooms can ride vehicle rooms (source parse) |
| Process shape | one Node process (consumer + WS + API) via `pnpm dev`; compose service in 8.4b | MVP simplicity; split later if load demands |
| Env | `KAFKA_BROKER` / `DATABASE_URL` / `REDIS_URL` / `PORT` (compose-matching defaults) | same posture as bridge env config |

## Risks

| Risk | Mitigation |
|---|---|
| At-least-once duplicates from Kafka redelivery (real — 6 in the Phase-7 soak) | triple net: manual-commit-after-success + Redis NX dedupe + PG `ON CONFLICT DO NOTHING`; restart test is the gate |
| Redis outage silently dropping dedupe | documented fail-open (process anyway; PG still guards); alerts on the error path |
| Schema drift edge↔backend | shared `/schemas` via the existing ajv validators — the Phase-0 contract-freeze tests extend to consumed records |
| Test pollution of the dev PG | integration tests insert run-prefixed ids + truncate their own rows; no shared-table assertions without filters |
| Consumer stuck on poison message | processor failure → no commit → redelivery loop (visible in logs); poison-DLQ deferred to Phase 10 (documented) |
| Latency gate flakiness under load | local single-broker measurements; the 1 s criterion is measured publish→WS-receipt, not browser render (render is Gate 9) |

## Open questions — resolve at approval

1. **Fastify (proposed) vs Express** — Fastify for TS/schema fit; say the word to swap.
2. **Single `events`-as-incidents table (proposed)** vs separate incidents table — proposed single; splitting later is a migration, not a rewrite.
3. **Live-state TTL 300 s** — approve or edit (operator-visible staleness).
