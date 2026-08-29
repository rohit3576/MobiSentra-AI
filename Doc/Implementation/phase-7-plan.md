# Phase 7 — Edge Messaging: Spool → MQTT → Bridge → Kafka · Plan

> **Status: APPROVED 2026-08-29 (owner: "apprved" — defaults locked as
> proposed); execution one-by-one per the working agreement.**
> Source of truth: `implementation-sequence.md` Phase 7 (on conflict, the
> runbook + `implementation-plan.md` §2 win).
> **No owner-input steps** (no creds, no datasets). Environment prerequisite
> for integration steps only: the dev stack (`docker compose -f
> infra/docker-compose.yml up -d` — EMQX + Kafka + bridge, Phase 0).
> Grounding facts verified in code 2026-08-29 (survey below).

## Objective

Zero event loss from a moving vehicle: the Phase-6 envelope stream leaves
the edge through a **write-ahead SQLite spool** (persist BEFORE publish) →
MQTT QoS 1 to EMQX → the existing bridge → Kafka. On failure/blackout the
spool retains; on reconnect it replays in order; delivery is at-least-once
with the CloudEvents `id` as the dedupe key (Kafka-side dedupe lands in
Phase 8 — Gate 7 asserts edge-layer behavior per the runbook).

**Gate 7:** 10-minute network blackout during active events → all events
arrive post-reconnect · zero loss, zero duplicates after dedupe · broker
kill/restore mid-stream → no crash, full replay.

## Grounding facts (2026-08-29, verified in code)

| Item | Fact (where) | Consequence for us |
|---|---|---|
| Edge messaging package is empty | `edge/mobisentra/messaging/` = `.gitkeep` only (reserved since Phase 0); `paho-mqtt>=2.0` already a runtime dep | Everything Phase 7 builds is new; paho **v2 callback API** (`CallbackAPIVersion`) |
| Bridge exists and works | `bridge/src/index.ts` (Phase 0): subscribes `mobisentra/#` QoS 1, forwards to Kafka with `/`→`.`; stateless; its README carries the exact Phase-7 hardening backlog (backpressure, counters, duplicate suppression) | 7.3 = hardening an existing service, not building one; **topic contract: publish to `mobisentra/events` (slash)** — the documented Phase-0 gotcha (dots don't match `#`) |
| Dev stack ready | `infra/docker-compose.yml`: kafka 4.0 KRaft (localhost:9092), emqx 5.10.3 (1883), bridge service, health checks | Integration/gate runs use the sanctioned local stack; unit tests never require it |
| Envelope stream is the input | `EventEngine.process()` → envelope dicts (6.3a wires them to the JSONL sink today); CloudEvents `id` is already a UUID per envelope | Publisher slots in beside the JSONL sink; dedupe key = envelope `id` for free |
| No MQTT tests exist today | `tests/` has no mqtt/messaging tests; suite culture = stubs + fakes, integration gated | Unit layer uses an injectable transport (stub MQTT client); real-stack runs are scripts + recorded evidence (the fight-benchmark pattern) |
| Long-run sensitivity | Owner stop directives (2026-08-28) killed long CV benchmarks | CI-able tests run in seconds; the 10-min gate soak is a one-shot evidence script with a `--minutes` knob, never a default test |

## Steps (summary — runbook 7.1–7.4)

| Step | Plan | Done when |
|---|---|---|
| 7.1 publisher + spool | `messaging/spool.py` (SQLite write-ahead queue, dedupe on `id` at write) + `messaging/publisher.py` (paho QoS 1, replay on reconnect, injectable transport) wired beside the JSONL sink | unit tests: publish fail → retained; reconnect → replayed once; duplicate id → not re-sent |
| 7.2 → Kafka integration | publish real envelopes through EMQX + bridge; consume `mobisentra.events` and verify intact | published events appear in Kafka via the Phase-0 bridge rule |
| 7.3 bridge hardening | backpressure (pause MQTT consumption while Kafka producer disconnected), forward/drop counters, optional duplicate suppression; `bridge/README.md` env docs | kill-switch scenarios (7.4) pass through the gateway without loss |
| 7.4 fault-injection | (a) broker kill mid-stream; (b) network partition during active events; (c) forced duplicate delivery → spool/QoS-1 assertions | all scenarios pass at the edge layer; Gate-7 evidence recorded |

## Execution division (draft for approval — one-by-one, each independently verifiable)

**Dependency / blocking map:**

```
7.1a spool (pure SQLite, tmp-file tests)
   → 7.1b publisher (write-ahead + replay; stub transport unit tests)
   → 7.1c pipeline wiring (messaging.yaml + main.py flags; JSONL sink stays)
7.2  real-stack integration (needs docker stack up; gated skip)
7.3a bridge: extract + unit-test pure pieces (topic map, suppression cache) — pnpm/vitest
7.3b bridge: backpressure + counters + env docs (integration via 7.4)
7.4a resilience unit tests (stub transport: kill/fail/duplicate scenarios)
7.4b gate evidence script (tools/messaging_soak.py: real stack, blackout,
     broker kill/restore; JSON evidence; --minutes knob)
```

Proposed execution order: **7.1a → 7.1b → 7.1c → 7.2 → 7.3a → 7.3b →
7.4a → 7.4b** (7.3a can run any time after 7.1a; kept late to keep the
edge side sequentially clean).

### 7.1a — SQLite spool (no owner input) ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| `SpoolQueue`: WAL-mode SQLite at `runs/spool/<camera or edge>.db` (single edge-wide spool — one vehicle, one queue); schema per runbook (`id` PK, `topic`, `payload`, `created_at`, `sent`); `enqueue` (INSERT OR IGNORE — duplicate `id` is a counted no-op), `pending(batch)` in insertion order, `mark_sent(ids)`, `stats()` (pending/total/dropped); retention cap with drop-oldest + dropped counter | `edge/mobisentra/messaging/spool.py` + `tests/test_spool.py` | ✅ **11/11 tests green** (suite 327→338, ruff clean): FIFO round-trip incl. continuation after partial sends; batch sizing; duplicate id → one row + `enqueue` False; **dedupe survives sends** (at-least-once replay of a sent id never re-queues); `mark_sent` idempotent; cap drops oldest with persisted dropped-counter; **crash surrogate** — reopen → rows, order, counter intact; field/batch/cap validation. Design notes: (1) thread-safety built in now (`check_same_thread=False` + lock) so the 7.1b paho callback thread needs no rework; (2) SQLite cannot index `rowid` — the 100k cap bounds pending scans to ms at drain-tick frequency, so no sequence column (documented in-module); (3) dropped-counter persists in `spool_meta` (survives restarts, part of stats) |

### 7.1b — MQTT publisher, write-ahead + replay (consumes 7.1a) ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| `EventPublisher`: `publish(envelope)` = spool.enqueue → async drain (publish QoS 1 to `mobisentra/events` → PUBACK → mark_sent); on no-connection or NACK: row stays pending, drain retries with backoff; on reconnect: replay pending FIFO; **never blocks the pipeline** (enqueue is a fast local write; network happens in the drain loop). Transport = injectable MQTT client (paho v2 in production; stub in tests) | `edge/mobisentra/messaging/publisher.py` + `tests/test_publisher.py` | ✅ **11/11 tests green** (suite 338→349, ruff clean): fail → row retained (zero wire contact); reconnect → replayed **exactly once** (PUBACKed ids never re-sent across repeated drains); duplicate enqueue → single send; payload = byte-intact `json.dumps(envelope)` on the configured topic; partial batch (broker dies mid-batch) → delivered-so-far marked, rest retained, next pass completes; batch sizing (2+2+1); empty-spool noop; backoff 1→2→4→…→60 cap (pure function, no sleeps); missing/empty envelope `id` + ctor validation; **background-loop smoke** (start → 4 publishes drain within 2 s without explicit drain calls → stop). Design notes: (1) transport = synchronous `deliver()` protocol (return = PUBACKed, raise = not delivered) — paho v2 adapter implements it via `wait_for_publish` and lands with 7.1c wiring, keeping publisher.py stdlib-only; (2) `drain_once` public — deterministic tests + the 7.4b soak drive it directly, the daemon loop (woken by publishes, exponential backoff) is thin integration surface |

### 7.1c — Pipeline wiring + config ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| `configs/messaging.yaml` (url, topic, client_id, spool path+cap, replay batch, backoff) + strict loader (severity.yaml pattern); `main.py` `--messaging-config` + `--publish` flag (default off: sample runs stay local — runs-without-hardware); `pipeline.py`: envelopes → JSONL sink **and** publisher when attached | `edge/configs/messaging.yaml`, `edge/mobisentra/messaging/config.py`, `messaging/transport_paho.py`, `pipeline.py`, `main.py` + `tests/test_messaging_wiring.py` | ✅ **13/13 tests green** (suite 349→362, ruff clean) + CLI guard proven live (`--publish` without `--detect` → exit 1 + message). Loader: exact-key strictness, url scheme allowlist (mqtt/mqtts/ws/wss), positive numbers, malformed YAML — all `MessagingConfigError` with path; **the Phase-0 dotted-topic gotcha is now a hard config error** (`mobisentra.events` rejected with a slash hint). Approved defaults locked by test (url/topic/client_id/spool 100k/batch 500/backoff 1→60/PUBACK 10s). Wiring: `attach_messaging` builds ONE edge-wide publisher (identity-shared across cameras, stub-transport injectable, `start=False` for deterministic drains); run_frame emits envelopes to **both** the JSONL sink and the wire (counts match); `MessagingHandle.shutdown()` = stop loop → final best-effort drain → transport close (stub-verified); `--publish` off = zero messaging behavior — no spool file materializes, default runs unchanged. `PahoTransport` (paho v2 `CallbackAPIVersion.VERSION2`): lazy connect (broker-down-at-startup = blackout-from-t=0, spooled), PUBACK-synced `deliver` with timeout, auto-reconnect delays 1→60s — live-proofed in 7.2 |

### 7.2 — Real-stack integration (environment: docker stack up) ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| Script-driven round trip: publisher → EMQX → bridge → Kafka topic `mobisentra.events`; consume and compare against the sent envelopes (id set + byte equality); test auto-skips with a clear message when the broker is unreachable | `edge/tests/test_messaging_integration.py` (gated) + `tools/messaging_check.py` (manual CLI) | ✅ **Live green, first run** — test: **100 envelopes → 100 in Kafka, byte-intact, in publish order** (~11.5 s; single partition: the bridge produces with a constant key → total order); CLI tool: 50/50 ordered byte-intact PASS, drain 1.0 s, spool cleared. **PahoTransport live-proven** (lazy connect, QoS-1 PUBACK sync against real EMQX). Consumer = kafka-console-consumer via `docker compose exec` (the project's documented smoke pattern — zero new Python deps). Gate: EMQX socket probe + `docker compose ps` state; **skip path proven by test** (dead port → False, bogus compose file → False); skip message carries the compose-up instruction. Run isolation: unique id prefix per run (`it-<hex>-NNNN` / `chk-<hex>-NNNN`) — old topic content can never pollute a verdict. Stack facts recorded: `KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"` (topic self-created by the bridge's first produce) |

### 7.3a — Bridge pure pieces extracted + unit-tested ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| Extract from `index.ts`: topic mapping (`/`→`.` with prefix guard) and a bounded TTL id-cache suppressor (default OFF); unit-test both under the bridge's pnpm workspace (vitest, dev-dep only) | `bridge/src/lib/topics.ts`, `bridge/src/lib/suppress.ts`, `bridge/test/*.test.ts`, `package.json` (+vitest 4.1, `test` script) | ✅ **vitest 10/10 + `tsc --noEmit` clean.** `mapTopic`: canonical + nested slashes → dots; foreign/bare prefixes → null; **the Phase-0 dotted-topic gotcha maps to null (dropped, never forwarded as-is)**; configurable prefix honored. `createSuppressor`: disabled (default) never suppresses; redelivery within TTL suppressed + counted; TTL expiry re-admits (injected clock); **bounded memory — oldest evicted at max, proven by the eviction test**; unidentifiable payloads (non-JSON / missing / non-string / empty id) always forwarded. `index.ts` rewired to `mapTopic` immediately (single source of truth; behavior identical to the inline logic, no restart needed until 7.3b). Test-logic fix during the loop: the eviction case initially asserted the wrong resident after re-admission (bound=2 means re-admitting one evicts another) — implementation was correct, the assertion order was not |

### 7.3b — Bridge backpressure + counters + docs ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| Pause MQTT message handling while the Kafka producer is disconnected (buffer with a bound → drop-with-counter when exceeded — backpressure must not OOM the bridge); periodic counters log (forwarded/dropped/suppressed/buffered); env config documented in `bridge/README.md` | `bridge/src/lib/buffer.ts` + `bridge/test/buffer.test.ts`, `bridge/src/index.ts` (rewrite), `bridge/README.md` | ✅ **vitest 15/15 + tsc clean + live kill-rehearsals through the real container.** `createBoundedBuffer`: FIFO, peek/pop, **oldest dropped + counted at the bound** (unit-proven). Bridge rewrite: suppressor wired (default OFF), counters log (forwarded/dropped/suppressed/buffered/kafka), env config (BUFFER_MAX/DEDUPE_*/COUNTER_INTERVAL_S) documented in README. **Backpressure shape changed from the plan's "pause" and for a reason:** mqtt.js v5 has no pause(), and unsubscribing is LOSSY (broker accepts, nobody delivers) — the lossless mechanism is **MQTT disconnect with a persistent session** (fixed clientId + clean:false): EMQX queues QoS-1 for the offline bridge and flow-controls producers; already-delivered messages land in the bounded buffer. **Live findings (two flaws found by the rehearsals, both fixed):** (1) the error-code match never fired for this librdkafka failure mode → trigger now matches code −195 / isFatal / the actual "all broker connections are down" text; before the fix, messages rode **librdkafka's internal retransmit queue** — verified 20/20 delivered after a short outage even with backpressure not engaging (layered safety, recorded); (2) **buffer-gated recovery deadlocked** — in the disconnect epoch messages queue at EMQX, the buffer stays empty, a produce-driven probe never fires → recovery now uses a **watermark-query probe** (true broker round-trip every 2 s; the invariant is comment-documented in code). **Definitive rehearsal (no bridge restart):** kafka killed → `mqtt disconnected, buffering` → 10 published (EMQX-held; edge spool cleared via broker PUBACKs) → kafka restored → `kafka back (watermark probe)` → mqtt reconnected → **10/10 exact ids in order in Kafka**. Also proven: EMQX session queue flushed 10/10 through a bridge restart |

### 7.4a — Fault-injection, edge layer (stub transport; CI-able) ✅ 2026-08-29

| Do | Files | Done when |
|---|---|---|
| (a) broker kill mid-stream = transport raises/disconnects during active publishing → no crash, rows retained, replay on "reconnect"; (b) partition = extended unreachable window with continued enqueue → backlog drains FIFO after; (c) forced duplicate delivery (QoS 1 at-least-once: PUBACK lost → broker redelivers) → publisher idempotence (already-sent id from spool state) and no spool re-send | `edge/tests/test_messaging_resilience.py` | ✅ **5/5 in 0.06 s** (suite 364→369, ruff clean) — no real broker, no sleeps, deterministic manual drains. **(a) kill mid-stream:** healthy epoch delivers; broker dies → publishes keep flowing (no crash), repeated drain ticks all fail cleanly, wire untouched, 3 rows retained; heal → replay in order, spool empty. **(b) partition-from-t0** (the lazy-connect blackout path): 3 batches × 3 with failed ticks between → zero wire leakage during the partition, 9-deep backlog drains strict FIFO in one reconnect pass. **(c) PUBACK lost:** the wire sees 2 sends (at-least-once, recorded by the broker-side stub), the spool holds exactly 1 record ever — redelivery acked once, no third send, re-enqueue refused (the Phase-8 dedupe anchor). **Crash-recovery narrative:** partition → process death (no graceful stop) → fresh spool+publisher on the same disk → 5-row backlog replays FIFO. **Partial delivery:** broker dying mid-batch never loses delivered-so-far acks. Transport = `FlakyTransport` (down epochs + per-id PUBACK loss that records the broker-side receipt — the honest at-least-once shape) |

### 7.4b — Gate-7 evidence run (real stack; one-shot, recorded)

| Do | Files | Done when |
|---|---|---|
| `tools/messaging_soak.py`: drives the publisher with scripted envelopes; phases = steady stream → **blackout** (publisher network cut / EMQX stopped `--minutes`, default 10 for the gate; CI never runs this) → reconnect → broker kill/restore mid-stream; consumes Kafka at the end; writes JSON evidence (sent ids, received ids, dupes, timeline) + asserts zero loss / full replay | `edge/tools/messaging_soak.py` + `edge/runs/messaging-soak.json` (evidence, gitignored dir) | Gate-7 table filled: 10-min blackout → all arrive post-reconnect; kill/restore → no crash, full replay; duplicates = 0 after id-dedupe of the received stream |

## Gate 7 — checklist (from the runbook)

- [ ] 10-minute network blackout during active events → all events arrive post-reconnect
- [ ] Zero loss, zero duplicates after dedupe
- [ ] Broker kill/restore mid-stream: no crash, full replay

## Proposed defaults (approve or edit — approval locks them)

| Item | Default | Why |
|---|---|---|
| MQTT topic | `mobisentra/events` (single, slash) | the bridge contract + Phase-0 gotcha; envelope `source`/`camera_id` already carries per-camera identity — no need for topic fan-out pre-Phase-8 |
| Spool location / cap | `runs/spool/edge.db`, WAL; cap **100k events** (drop-oldest + dropped counter) | hours-to-days of headroom at realistic event rates; bounded disk on vehicle hardware; cap value is operator-tunable in messaging.yaml |
| Replay batch | 500 per drain tick | keeps PUBACK bookkeeping tight; sub-second ticks |
| Backoff | 1 s initial, ×2, max 60 s | reconnect storms must not spin the CPU |
| Bridge suppression | OFF by default, `DEDUPE_TTL_MS` + `DEDUPE_MAX` env when on | bridge stays stateless by default (its documented philosophy); Phase-8 consumer dedupe is the real guarantee |
| `--publish` flag | default OFF | runs-without-hardware: sample runs never require a broker; production turns it on |
| Soak duration | gate run = 10 min (the gate value); `--minutes` knob for shorter rehearsals | owner's long-run directive: no long runs by default, the gate soak is explicit and one-shot |

## Risks

| Risk | Mitigation |
|---|---|
| paho v2 API friction (callback API changed from v1) | transport injected from day one; paho only imported in the production adapter; v2 patterns verified in 7.1b against real EMQX in 7.2 |
| Spool grows unbounded during multi-day partitions | hard cap + drop-oldest + dropped counter surfaced in stats (and later metrics); default 100k |
| Publisher blocking the CV pipeline on network | architecture: enqueue (local write) is the only synchronous step; drain is background — wiring tests assert pipeline latency unchanged |
| Bridge OOM under Kafka outage while MQTT keeps flowing | bounded buffer with drop-counter (7.3b) — drops are counted, and the edge spool still holds the source of truth for replay |
| Integration tests flaky when stack is down | auto-skip with explicit reason; the gated path is itself tested |
| Duplicate semantics confusion (QoS 1 = at-least-once everywhere) | runbook stance kept: edge asserts spool idempotence; exactly-once is claimed only after Phase-8 consumer dedupe — soak reports pre- and post-dedupe counts |

## Open questions — resolve at approval

1. **Defaults table above** — approve as-is or edit (all operator-tunable later via messaging.yaml / bridge env).
2. **Single topic vs per-camera topics** — proposed single `mobisentra/events` (bridge contract; Phase 8 can fan out server-side if the consumer wants it). Alternative `mobisentra/events/<camera_id>` costs nothing at the edge but multiplies Kafka topics — rejected for now.
3. **Gate soak length** — proposed: run the full 10-minute blackout once for gate evidence (≈13 min wall clock, one-shot script, never in CI); rehearsals at `--minutes 1`. Alternative: accept a shorter blackout for the gate (owner call — the runbook says 10).
