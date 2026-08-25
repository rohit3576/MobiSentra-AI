# Phase 0 — Completion Report

> **Status: COMPLETE** (2026-08-25) · Gate: **PASSED** (one item activates after first push)
> Scope executed: `Doc/Implementation/implementation-sequence.md` Steps 0.1 – 0.10 + Gate 0
> Repo: https://github.com/rohit3576/MobiSentra-AI

---

## 1. Gate 0 — Evidence

| Gate criterion | Result | Evidence |
|---|---|---|
| `docker compose up` starts the full stack | ✅ | 6 services up; kafka/emqx/postgres/redis healthy, bridge running |
| MQTT→Kafka round-trip with a test message | ✅ | CloudEvents envelope `smoke-0003` traveled **MQTT → EMQX → bridge → Kafka → console consumer**, byte-intact (2026-08-25) |
| Local checks green | ✅ | edge: ruff clean + **16/16 pytest** · backend: tsc strict clean + **6/6 vitest** · bridge: tsc clean |
| Event schema v0 frozen, validated from both languages | ✅ | Same JSON Schemas contract-tested in `edge/tests/test_schemas.py` and `backend/src/schema/validate.test.ts` |
| CI green on GitHub Actions | ⏳ pending first push | Workflow validated locally (YAML valid, 3 jobs); runs when owner pushes |
| Clone test (stranger runs quickstart) | ⏳ pending first push | Compose round-trip verified from this machine; true clone test after repo is public/pushed |

## 2. What Was Built

### Hygiene
- `LICENSE` — canonical AGPL-3.0 (fetched from gnu.org, 661 lines)
- `README.md` — pitch, architecture diagram, quickstart, repo layout, responsible use, vendored license badge + icon logo
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `CHANGELOG.md`
- `.github/ISSUE_TEMPLATE/` — bug report, feature request (both with the fits-without-hardware question), config pointing at Discussions

### Monorepo
```
edge/       Python (uv): mobisentra package — ingestion/ vision/ analytics/ events/ messaging/
backend/    Node 22 + strict TS: schema validator (ajv), tests (vitest)
bridge/     MQTT→Kafka gateway service + Dockerfile (in compose)
dashboard/  placeholder (Phase 9)
mlops/      placeholder (Phase 10)
infra/      docker-compose.yml + README
schemas/    CloudEvents v0: envelope, event, detection, alert, analytics + example
Doc/        plan.md, implementation-plan.md, handoff, Implementation/ runbook + this report
```

### Dev stack (`infra/docker-compose.yml`)
| Service | Image | Host port |
|---|---|---|
| Kafka | `apache/kafka:4.0.0` (KRaft, no ZooKeeper) | 9092 (host) / 29092 (internal) |
| EMQX | `emqx/emqx:5.10.3` | 1883 MQTT / 18083 dashboard |
| bridge | built from `../bridge` | — |
| PostgreSQL | `postgres:16` | 5432 |
| Redis | `redis:7-alpine` | 6379 |
| MLflow | `ghcr.io/mlflow/mlflow:v3.1.0` | **5001** (5000 blocked by macOS AirPlay) |

One command: `docker compose -f infra/docker-compose.yml up -d`

### Edge package
- Camera registry loader (`edge/mobisentra/ingestion/config.py`): frozen dataclasses (`CameraConfig`, `ZoneConfig`, `Thresholds`), normalized-polygon validation, threshold validation, duplicate-ID detection, empty-zone (`restricted_zone: []`) tolerance
- `edge/configs/cameras.yaml` (3 sample cameras) + `sample-cameras.yaml` (documented example)
- 16 tests: loader edge cases + schema v0 validation

### Schema v0 (frozen contract)
- CloudEvents 1.0 envelope: `id`+`source` = idempotency key; `source` must match `^/mobisentra/`, `type` must match `^org\.mobisentra\.`
- Per-type payloads: event (severity enum, confidence 0–1, model_versions, evidence_ref), detection (normalized bboxes, track IDs), alert (lifecycle: RAISED→ACKNOWLEDGED→ESCALATED→RESOLVED), analytics (occupancy levels)
- Kafka topics: `mobisentra.detection` / `.events` / `.alerts` / `.analytics`

### CI (`.github/workflows/ci.yml`)
- `edge`: uv sync → ruff → pytest
- `backend`: pnpm → tsc --noEmit → vitest
- `bridge`: pnpm → tsc --noEmit
- pnpm pinned `version: 11.23.0` in `pnpm/action-setup` (actions run from repo root and can't read nested `packageManager` fields)

## 3. Architecture Correction (important)

**Planned:** EMQX rule-engine bridges MQTT→Kafka (per original `implementation-plan.md` §2).

**Reality (verified empirically 2026-08-25, EMQX 5.8.3 and 5.10.3):** EMQX **open-source ships no Kafka connector** — the Kafka bridge is an Enterprise feature. OSS image contains only `emqx_bridge` (HTTP) and `emqx_bridge_mqtt` apps. API attempts returned `unknown connector type` / crashed on `binary_to_existing_atom(<<"kafka">>)`.

**Fix shipped:** own thin gateway service in `bridge/` — Node + `@confluentinc/kafka-javascript` (librdkafka). Subscribes `mobisentra/#` (QoS 1) → produces to Kafka with **topic mapping `/` → `.`**. Stateless; at-least-once; durability handled by edge spool (Phase 7) and consumer dedupe (Phase 8). Runs inside compose; same service deploys to any VPS.

**Topic scheme (locked):** MQTT topics use **slashes** (`mobisentra/events`); Kafka topics use **dots** (`mobisentra.events`). ⚠️ A dot is not a separator in MQTT — `mobisentra.events` will never match `mobisentra/#`. This exact mistake caused the first smoke-test failure; documented in `bridge/README.md` so it never repeats.

Docs corrected everywhere: `implementation-plan.md` §2 + Phase 7, runbook Step 0.8 + 7.3, both READMEs, CHANGELOG.

## 4. Issues Hit & Resolutions

| Issue | Resolution |
|---|---|
| Port 5000 occupied (macOS AirPlay/ControlCenter) | MLflow host port → 5001 |
| pnpm not installed | `npm i -g pnpm` (11.23.0) — dev tool, not a repo change |
| pnpm 11 blocks postinstall scripts | `pnpm-workspace.yaml` `allowBuilds: esbuild / @confluentinc/kafka-javascript` per package (v11 moved this setting out of package.json) |
| EMQX default `admin/public` rejected by REST API | Dev dashboard password set via `EMQX_DASHBOARD__DEFAULT_PASSWORD` env in compose |
| kafka-javascript ESM interop (`Kafka` not exported at runtime) | Import from `RdKafka` namespace; librdkafka-style `Producer` with callback `connect()` + `setPollInterval` |
| ajv 8: no `date-time` format, CJS interop | Named `Ajv` import + own RFC 3339 regex via `addFormat`, exported `createAjv()` factory |
| MQTT→Kafka round-trip initially empty | Root cause: dot-vs-slash topic semantics (see §3) — not an EMQX bug |
| EMQX 5.8.3 suspicion during debugging | Upgraded to 5.10.3 anyway (kept) |
| First CI run failed: "No pnpm version specified" | `version: 11.23.0` pinned in action-setup steps |

## 5. Verification Commands (re-run anytime)

```bash
# edge
cd edge && uv run ruff check . && uv run pytest

# backend
cd backend && pnpm typecheck && pnpm test

# bridge
cd bridge && pnpm typecheck

# stack + round-trip
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml exec kafka \
  /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic mobisentra.events \
  --from-beginning --timeout-ms 8000
```

## 6. Deferred to After First Push

- [ ] CI badge green (workflow runs on push)
- [ ] Close clone-test checkbox in runbook Gate 0
- [ ] Enable GitHub Discussions (Settings → Features) so the issue-template link works
- [ ] Optional: GitHub repo social preview = `.github/assets/icon.png`; repo "About" description from implementation-plan §0

## 7. Conventions Established

- **Owner runs 100% of git operations.** Every handoff = changed-file list + suggested commit message; agent never stages/commits/pushes.
- Sample-data-first: nothing in Phase 0–9 requires hardware; `sample://` sources are default in the camera registry.
- Bundled demo videos whitelisted in `.gitignore` (`!edge/sample_data/videos/`).
- Every phase ends with a measurable gate recorded in this folder.

## 8. Next

**Phase 1 — Video Ingestion (1 week):** StreamReader (latest-frame-only), watchdog + auto-reconnect, source adapters (sample/MP4/webcam/RTSP), FPS throttling, tests + 60-min soak. Gate: < 200 ms lag, auto-recover < 15 s, no crashes.

Runbook: [`implementation-sequence.md`](./implementation-sequence.md) → Phase 1.
