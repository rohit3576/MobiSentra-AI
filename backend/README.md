# MobiSentra Backend

Node/TypeScript service consuming `mobisentra.events` from Kafka into
PostgreSQL (durable history), Redis (live camera state) and Socket.IO
(real-time push) — plus the REST API the Phase-9 dashboard consumes.
One process, one port (`pnpm dev` locally, a compose service in the dev
stack).

## Run

```bash
# against the dev stack (needs kafka/postgres/redis up — see ../infra)
pnpm dev                 # http://127.0.0.1:3000 (REST + Socket.IO)

# as a compose service (no host-side tooling needed)
docker compose -f ../infra/docker-compose.yml up -d --build backend
```

Migrations apply automatically on boot (idempotent; `pnpm migrate` runs
them standalone).

> Run the backend **either** on the host **or** as the compose service —
> never both at once: the Kafka group gives the single partition to one
> consumer, and the other silently idles (or steals your test traffic).

## Test / verify

```bash
pnpm typecheck           # strict tsc
pnpm test                # unit + gated integration suite (87 tests)
```

The integration suite runs against the live compose stack and **skips
with instructions** when kafka (9092) / postgres (5432) / redis (6379)
are unreachable — CI stays green without Docker.

## Environment

| Var | Default | Notes |
|---|---|---|
| `KAFKA_BROKER` | `localhost:9092` | compose uses `kafka:29092` (INTERNAL listener) |
| `KAFKA_TOPIC` / `KAFKA_GROUP` | `mobisentra.events` / `mobisentra-backend` | manual commits after success — at-least-once, collapsed by dedupe + `ON CONFLICT` |
| `DATABASE_URL` | `postgres://mobisentra:mobisentra@localhost:5432/mobisentra` | dev-stack default |
| `REDIS_URL` | `redis://localhost:6379` | dedupe keys (24 h TTL) + `camera:{id}` live state (5 min TTL) |
| `PORT` / `HOST` | `3000` / `0.0.0.0` | REST + Socket.IO share the port (`/socket.io` path) |
| `EVIDENCE_ROOT` | `../edge/runs/evidence` | backs `GET /api/evidence/*` (traversal-sandboxed, Range-capable) |

## API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/incidents` | list; filters `severity, camera, vehicle, since, until, acked, limit` |
| GET | `/api/incidents/:id` | detail incl. `evidence_ref` + escalation |
| GET | `/api/cameras` | PG distinct + Redis online status |
| GET | `/api/events` | raw history, keyset cursor paging (`cursor`, `limit`) |
| POST | `/api/incidents/:id/ack` | body `{actor}` — writes an audit row |
| POST | `/api/incidents/:id/escalate` | body `{actor, detail?}` — writes an audit row |
| GET | `/api/evidence/*` | mp4 clips from `EVIDENCE_ROOT` |

Socket.IO: `subscribe` with a `vehicleId` (+ ack callback) joins
`alerts:{vehicle}` (event pushes) and `cameras:{vehicle}` (occupancy
state pushes).

## Layout

```
src/index.ts      process assembly (boot migrations, supervisor, graceful stop)
src/consumer/     kafka wrapper (manual commits), redis dedupe, write-path pipeline
src/api/          fastify server + PG/Redis stores
src/ws/           Socket.IO push server (rooms)
src/schema/       ajv validators (shared /schemas) + SQL migrations + runner
test/             unit suites + test/integration (gated, live stack)
```
