HANDOFF CONTEXT — Session Log 2026-08-29 session 2 (Phase 8 planned + steps 8.1b, 8.1a, 8.2 executed; continuation of handoff-2026-08-29.md — same day, Phase 7 was closed in that arc)
====================================================================================================================================================================================

Project: MobiSentra AI
Session: continuation of Doc/handoff-2026-08-29.md (same calendar day; owner
committed that arc through 35c3d01 + the handoff, then 8aebaf2/a96034f/eb17a48
for the Phase-8 plan and steps 8.1b/8.1a).

USER REQUESTS (AS-IS)
---------------------
- "now whats next ?"
- "Phase 8 — Backend Services (the main event) lets start the plan and as we
  said small small divide the doc"
- "start with the 8.1b"   (deliberate start at the consumer step, not 8.1a)
- "ok start with the 8.1a"
- "ok start"              (= 8.2)
- "hey sorry internext connect was bad continue"  (work had landed fine;
  the disconnect only broke the todo bookkeeping)
- "save the chat"

WORK COMPLETED THIS SESSION
---------------------------

1. PHASE 8 PLAN DRAFTED + APPROVED (division 8.1a–8.4b)
   - Doc/Implementation/phase-8-plan.md: grounded on the Phase-0 backend
     skeleton (ajv contract validation exists in src/schema/events.ts; api/
     consumer/ws dirs are .gitkeep), live compose stack (pg16/redis7/kafka
     all exposed + healthy), and the flowing mobisentra.events topic.
   - Division: 8.1a record mapping → 8.1b consumer → 8.2 dedupe → 8.3a PG
     schema/runner → 8.3c Socket.IO → 8.3b write-path pipeline → 8.4a REST
     → 8.5 integration/gate → 8.4b compose service.
   - Defaults proposed: triple duplicate net (manual-commit-after-success +
     Redis NX + PG ON CONFLICT), ioredis, pg + visible SQL migrations, no
     ORM, Fastify, single events-as-incidents table, live-state TTL 300s,
     one Node process. Runbook's "four topics" noted STALE — one canonical
     topic.
   - Owner approved implicitly by directing execution ("start with the
     8.1b") — recorded as approved-with-owner-directed step order.

2. STEP 8.1b — KAFKA CONSUMER WRAPPER (owner-directed first step)
   - backend/src/consumer/kafka.ts: EventConsumer (the policy loop) over a
     minimal ConsumerDriver protocol (edge-publisher Transport pattern):
     commit ONLY after the whole batch resolves; processor failure → run()
     rejects with zero commits and no further fetches (no silent skips —
     supervisor restarts, redelivery is safe); graceful stop finishes
     in-flight + commits + closes; sequential processing (bounded
     concurrency 1, per-partition order; knob deferred). LibrdkafkaDriver:
     group mobisentra-backend, manual commits, flow-mode queue, per-partition
     max offset + 1 resume semantics.
   - THREE LIVE CATCHES: (a) pnpm 11 allowBuilds gating silently blocked the
     native addon → workspace file now allows @confluentinc/kafka-javascript
     (4m47s build); (b) librdkafka commit() is SYNC FIRE-AND-FORGET — errors
     arrive via the offset.commit EVENT (verified from the shipped .d.ts, not
     assumed) → routed into the transport-error path; (c) a fake driver
     resolving instantly STARVES the event loop in microtasks (stop-timers
     never fire → real hang, killed orphaned vitest) → fake yields via
     setTimeout 0, comment-documented.
   - 6/6 tests (backend 12/12 incl. Phase-0 schema tests).

3. STEP 8.1a — ENVELOPE → TYPED EVENT RECORD
   - backend/src/lib/events.ts: toRecord() = ajv (shared schemas) FIRST,
     then runtime-guarded field extraction with ZERO casts — schema/code
     drift fails loudly at the parse point. Source parsing degrades
     gracefully (vehicle "unknown", camera from data) — never a crash.
   - CAMERA-PRECEDENCE DECISION (found by the shared example, which carries
     two different camera ids: source BUS_102/CAM_04 vs data
     BUS_102_CAM_04): data.camera_id WINS (it is the edge registry key the
     API queries); source segment is fallback-only. Comment-documented.
   - 14/14 tests (shared example end-to-end incl. raw passthrough; one
     envelope per kind; malformed rejections; optional fields → nulls;
     three source-fallback shapes).

4. STEP 8.2 — REDIS DEDUPE
   - backend/src/consumer/dedupe.ts: DedupeService over injectable
     DedupeStore; SET dedupe:{source}:{id} NX EX 86400 (runbook TTL,
     test-locked); RedisDedupe adapter maps OK→first / null→duplicate with
     exact SET args asserted; FAILS OPEN on store outage (process + log —
     PG ON CONFLICT is the never-blinking second net; documented as the
     deliberate trade).
   - ioredis added (pure JS). Type note: ioredis's class type imports as
     the NAMED export (import type { Redis }).
   - 5/5 tests (backend 31/31).

5. INTERNET DISCONNECT + RECOVERY
   - Owner's connection dropped mid-8.2; all work had landed. A system
     todo-continuation nudge arrived with stale statuses; every item was
     re-verified with evidence (files present, 31/31 green, all three doc
     recordings confirmed) before closing the todos.

COMMITS
-------
- Owner-run: 8aebaf2 (phase-8 plan), a96034f (8.1b), eb17a48 (8.1a).
- STAGED UNCOMMITTED at handoff: the 8.2 work (dedupe.ts, test, package.json
  + lockfile, and the plan/tracker/README ✅ recordings). Suggested message:
    "feat(backend): phase 8 step 8.2 — Redis SET-NX dedupe (fail-open on
     outage, runbook TTL locked); 5 tests"
- Then this handoff + its README row.

STATE AT PAUSE
--------------
- Phase 8: 8.1a ✅ 8.1b ✅ 8.2 ✅; remaining 8.3a (PG schema + ~40-line SQL
  migration runner, no ORM) → 8.3c (Socket.IO rooms) → 8.3b (write-path
  pipeline: validate→dedupe→PG→Redis→WS) → 8.4a (Fastify REST, 6 endpoints,
  audit) → 8.5 (integration suite + Gate 8) → 8.4b (compose service).
- Test state: backend 31/31 + tsc clean; edge 369+4; bridge vitest 15/15.
- Dev stack STILL UP (kafka/emqx healthy, postgres, redis, bridge,
  fake-cam, mediamtx, mlflow) — keep up for 8.3a/8.5.
- Project position: Phases 0–7 closed; v0.1.0 = Phase 8 + Phase 9.
- Parked owner decisions unchanged (fight production enablement, Phase 2 A′
  waive, Phase 3 occupancy counts, Phase 4 FP closure).

NEXT SESSION PICKUP LIST (in order)
-----------------------------------
1. Owner: commit staged 8.2, then this handoff.
2. Phase 8 Step 8.3a per phase-8-plan.md (events + audit_log tables, ordered
   .sql files, schema_migrations runner).
3. Continue one-by-one through 8.5 (Gate 8 evidence) and 8.4b.

KEY FILES
---------
- Doc/Implementation/phase-8-plan.md — step rows carry ✅ evidence + notes.
- backend/src/consumer/kafka.ts, backend/src/lib/events.ts,
  backend/src/consumer/dedupe.ts (+ matching backend/test/*.test.ts).
- backend/src/schema/events.ts (Phase-0 ajv contract — reused, unchanged).
- backend/pnpm-workspace.yaml (allowBuilds now includes kafka-javascript).

SESSION LESSONS
---------------
- pnpm 11 build-gates native addons SILENTLY: a dep that "installed fine"
  can be unimportable until allowBuilds names it. The error came only at
  import time — smoke-import new native deps immediately after install.
- librdkafka's commit() tells you nothing when it fails — errors surface
  on the offset.commit event. API semantics from the shipped .d.ts, not
  from memory or examples.
- An instantly-resolving promise in a consume loop = microtask-starvation
  deadlock: the loop never yields, macrotask timers (stop signals!) never
  fire, the suite hangs with zero output. Test fakes must yield to the
  event loop.
- The shared schema EXAMPLE is a spec: it exposed two different camera ids
  (source path vs data.camera_id), forcing the precedence decision at
  mapping time — where it belongs.
- Owner-directed step reordering (8.1b before 8.1a) was safe because the
  plan's dependency map kept them independent — the map earns its keep.
- After a connection drop, re-verify with evidence before trusting stale
  todo statuses — the work had landed; only the bookkeeping lagged.
