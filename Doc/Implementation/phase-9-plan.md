# Phase 9 — Dashboard · Plan

> **Status: APPROVED 2026-08-30** (working agreement: plan → approve →
> execute one-by-one). Owner decisions at approval: **9.6 auth dropped
> entirely**, occupancy thresholds approved as proposed, analytics
> Redis-only, demo replay tool yes. Planning done ahead of Phase-8
> completion on purpose — the two Phase-8 amendments below were approved
> WITH this plan so the not-yet-built 8.3b/8.4a land them right the
> first time.
> **Execution of Phase 9 starts only after Gate 8 passes** (house rule).
> Source of truth: `implementation-sequence.md` Phase 9 + `implementation-plan.md`
> Phase-9 section (on conflict, the locked plan wins).

## Objective

Operator-usable control center: live camera grid (status + occupancy
badges), active incidents with ack/escalate, incident detail with
evidence replay, filterable history — fed by the Phase-8 backend over
REST + Socket.IO. **Substance first:** incident-feed latency and
reliability over aesthetics; dark-ops styling, no chrome for its own
sake.

**Gate 9 (v0.1.0 — the "project exists" moment):**
- [ ] E2E: synthetic fall → dashboard incident < 2 s → ack → audit row
- [ ] **Clone test (release blocker):** fresh clone → compose up → quickstart works end-to-end
- [ ] `v0.1.0` tagged with demo video + `CHANGELOG.md`

## Phase-8 amendments (approve with this plan — affect 8.3b / 8.4a)

| # | Amendment | Why it can't wait |
|---|---|---|
| **A1** | **8.3b gains an analytics branch:** envelopes with `type` `org.mobisentra.analytics.*` → update Redis live state (`camera:{id}` hash: `occupancy_ratio`, `people_count`, `ts`) — **no PG row** (severity is NOT NULL in `events`; analytics have none) — and emit a `state` message on room `cameras:{vehicle_id}`. Validated by the analytics schema (extend the Phase-0 ajv set) | Without it the 9.2 occupancy badge has **no data source**; the current pipeline spec maps safety events only (severity required — analytics would fail mapping and drop) |
| **A2** | **8.4a gains an evidence route:** `GET /api/evidence/*` serving files from `EVIDENCE_ROOT` (env; demo default = edge evidence output dir), resolving `local://evidence/…` refs, **path-traversal-sandboxed**, `video/mp4` | `evidence_ref` in every envelope is a `local://` URI — clips live on the edge host. 9.4's "clip viewable from the incident" needs a serving path; fleet-wide clip upload is Phase 10+, documented |

Both amendments fold into the existing 8.3b/8.4a step rows (with tests) —
they are scope-locked here so the backend lands once.

## Grounding facts (2026-08-30, verified in code/docs)

| Item | Fact (where) | Consequence |
|---|---|---|
| `dashboard/` is empty | `.gitkeep` only (25 Aug) | greenfield scaffold; stack fixed by locked plan: **Vite + React + TS + Tailwind**, socket.io-client |
| Locked plan Phase-9 section | 4 screens; WS reconnect + per-vehicle room subscribe; auth off by default; "dark-ops, substance first" | the plan's Do-list below mirrors it 1:1 |
| Backend REST surface | 6 endpoints spec'd in phase-8-plan 8.4a (incidents list/detail, cameras+live, events history, ack, escalate) — **not yet built** | every 9.x after the scaffold consumes them → strict Gate-8 prerequisite |
| WS push already built (8.3c) | rooms `alerts:{vehicle_id}`, ack'd `subscribe` (guarded), channel `event`, payload = `EventRecord`, `path`+`corsOrigins` configurable | dashboard WS client has a stable contract to code against today |
| Gate-8 test harness (8.5) | integration suite publishes envelopes straight to Kafka | **reused as the demo-event generator** — no vision models needed in the loop for feed/latency verification |
| Analytics reality | `schemas/events/v0/analytics.schema.json` (`occupancy_ratio`, may exceed 1.0); edge emits them; `occupancy_level_change` is an event_type | amendment A1; badge thresholds are OUR decision (no enum in schemas) |
| Evidence reality | schema example: `local://evidence/{vehicle}_{cam}/{id}.mp4`; clips written by the edge evidence module | amendment A2; no upload path exists (by design until Phase 10) |
| Camera registry | `edge/configs/cameras.yaml` (+ documented `sample-cameras.yaml`); 8.4a `GET /api/cameras` = PG distinct + Redis live status | grid data source settled — dashboard never reads edge files |
| Ports in play | 1883/18083 EMQX, 9092 Kafka, 5432 PG, 6379 Redis, 5000 MLflow; backend `PORT` (8.4a) | dashboard dev 5173 (Vite default), compose host port **8080** — no collisions |

## Steps (division for one-by-one execution)

**Dependency / blocking map:**

```
Gate 8 passed ──→ 9.1a scaffold ──→ 9.1b live feed (<1 s) ──→ 9.3 incidents+ack/escalate ──→ 9.4 detail+evidence [A2] ──┐
                          │                                                                                             │
                          └──→ 9.2 camera grid [A1] ──→ 9.5 history+filters ──→ 9.7 demo+clone test+release (OWNER)
```

### 9.1a — Scaffold + shell

| Do | Files | Done when |
|---|---|---|
| `create-vite` (react-ts, current stable, locked at execution), Tailwind, strict tsconfig mirroring backend's posture; dark-ops app shell (header w/ connection status, vehicle selector, layout regions); typed API client (`/api` base via Vite dev proxy → backend `PORT`) + typed WS client (socket.io-client, reconnect ON, **re-subscribes rooms after reconnect**, subscribe-ack awaited); vitest + @testing-library/react wired; root CI extended | `dashboard/` (src/app shell, src/api/client.ts, src/ws/client.ts, tests) | `pnpm dev` renders the shell; unit test proves WS client re-subscribes its room set after a simulated reconnect; typecheck + tests green in CI without the stack |

### 9.1b — Live incident feed

| Do | Files | Done when |
|---|---|---|
| Feed panel: initial load via `GET /api/incidents`, live prepend via `event` channel on `alerts:{vehicle_id}` (room = selected vehicle); severity-colored rows; connection status reflects socket state | `dashboard/src/feed/*` + tests | with the 8.5 harness publishing, a feed row renders **< 1 s after backend emit** (measured emit→DOM, the Gate-8 criterion at the UI layer); reconnect mid-stream loses no already-rendered rows |

### 9.2 — Live camera grid `[A1]`

| Do | Files | Done when |
|---|---|---|
| Camera cards from `GET /api/cameras` (registry + online via live-state TTL); occupancy badge (Normal/Moderate/Crowded/Overcrowded from `occupancy_ratio` thresholds); live refresh via `state` messages on `cameras:{vehicle_id}` | `dashboard/src/cameras/*` + tests | grid reflects the registry; a published occupancy change flips a badge without refresh; stale camera shows offline |

### 9.3 — Active incidents + actions

| Do | Files | Done when |
|---|---|---|
| Newest-first incident list (live-merged with feed); ack + escalate buttons → `POST /api/incidents/:id/{ack,escalate}`; optimistic state, failure rollback | `dashboard/src/incidents/*` + tests | ack on a live incident clears it via WS/REST refresh; **audit row appears** (asserted in e2e against the stack); escalation marks the incident escalated |

### 9.4 — Incident detail + evidence replay `[A2]`

| Do | Files | Done when |
|---|---|---|
| Detail drawer/page: metadata, tracks, model versions, `occurred_at`; evidence clip player streaming `GET /api/evidence/*` from the envelope's `evidence_ref` | `dashboard/src/detail/*` + tests | the Gate-9 synthetic fall's clip plays from its incident row (8.5 harness event carrying a real clip under `EVIDENCE_ROOT`) |

### 9.5 — Event history + filters

| Do | Files | Done when |
|---|---|---|
| History table over `GET /api/events` w/ cursor paging; filters (camera, type, severity, time range) round-tripped as API params, not client-side only | `dashboard/src/history/*` + tests | filter changes refetch with visible URL/paging state; paging walks cursors without skips/dupes |

### 9.6 — Auth flag — **DROPPED by owner decision 2026-08-30**

The dashboard runs open single-user mode (personal project). If anyone
self-hosts seriously later, auth returns as a post-v0.1.0 backlog item;
audit logging of dashboard actions is unaffected (backend-side, always
on).

### 9.7 — Demo + clone test + release *(OWNER)*

| Do | Files | Done when |
|---|---|---|
| Standing acceptance demo: synthetic fall via laptop pipeline → incident < 2 s → ack → audit row; **demo replay tool** (replays `runs/events/*.envelopes.jsonl` → Kafka, compose `demo` profile — strangers get a living dashboard without model downloads); dashboard compose service (multi-stage: node build → nginx serving assets + proxying `/api`,`/socket.io`); demo GIF/video recorded; `CHANGELOG.md` | `tools/demo-replay.*`, `dashboard/Dockerfile`, `infra/docker-compose.yml`, `CHANGELOG.md` | Gate-9 checklist all green; **clone test**: fresh clone → `docker compose up` (demo profile) → events on dashboard, zero host tooling beyond Docker; owner tags `v0.1.0` + GitHub Release |

## Proposed defaults (approve or edit — approval locks them)

| Item | Default | Why |
|---|---|---|
| Stack | Vite + React + TypeScript(strict) + Tailwind, versions locked at 9.1a via create-vite current stable | locked-plan choice; no version archaeology in the plan doc |
| WS client | socket.io-client, builtin reconnect w/ backoff; room set re-subscribed + ack-verified after every reconnect | 8.3c contract; reconnect without re-subscribe = silent dead feed (the classic trap) |
| Dev wiring | Vite dev proxy `/api` + `/socket.io` → backend — **no CORS in dev**; compose: nginx same-origin proxy | one origin everywhere; `corsOrigins` only a prod escape hatch |
| Ports | dashboard dev **5173**; compose dashboard host **8080** | free ports on the current stack |
| Occupancy thresholds | ratio `<0.60` Normal · `<0.85` Moderate · `≤1.0` Crowded · `>1.0` Overcrowded | matches operator intuition (1.0 = capacity); locked with unit tests at 9.2 |
| Camera "offline" | live-state TTL (300 s, Phase-8 default) expired | consistent with backend truth; no second heartbeat protocol |
| Analytics persistence | **Redis live-state only** (A1); PG history for analytics = explicit non-goal for v0.1.0 | `events.severity` NOT NULL; analytics aren't incidents |
| Evidence serving | A2 route; `EVIDENCE_ROOT` default = edge evidence output dir (exact path confirmed at 9.4); traversal-sandboxed | demo-first; fleet upload = Phase 10+ (documented) |
| Demo feed | replay tool over the 8.5 publish harness; compose `demo` profile | clone test needs no GPUs/models/downloads |
| Frontend testing | vitest + @testing-library/react (unit, no stack); Playwright e2e **gated on stack reachability** (skip-with-instructions — the edge/Gate-8 pattern) | CI stays green without Docker; e2e proves the gates |
| Auth | none — dropped by owner decision (open single-user mode) | personal project; audit logging is backend-side and always on |

## Risks

| Risk | Mitigation |
|---|---|
| Latency gates flaky under load | measured emit→DOM locally, single broker; Gate-9's 2 s budget is generous vs Gate-8's 1 s wire budget — record numbers per run |
| Reconnect drops room subscriptions | re-subscribe + ack barrier is a 9.1a unit test, not a hope |
| Evidence route = arbitrary file read | resolve + prefix-check against EVIDENCE_ROOT, tested with traversal payloads |
| Analytics schema drift edge↔backend | shared-schema ajv validation (Phase-0 pattern) in the 8.3b branch |
| Clone test rot (the release blocker) | demo replay profile exercised in CI-optional job / pre-release checklist, not just once |
| Scope creep (video wall, fleet maps, pretty charts) | locked-plan screens only; everything else → backlog |

## Resolved at approval (2026-08-30, owner)

| # | Question | Decision |
|---|---|---|
| 1 | Auth 9.6 | **Dropped entirely** — open single-user mode; post-v0.1.0 backlog if ever |
| 2 | Occupancy thresholds | **Approved** — `<0.60` Normal · `<0.85` Moderate · `≤1.0` Crowded · `>1.0` Overcrowded |
| 3 | Analytics in PG too? | **No — Redis live-state only** (A1 as written) |
| 4 | Demo replay tool | **Yes** — compose `demo` profile; the Gate-9 clone test runs on it |
