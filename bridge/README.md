# MQTT → Kafka Bridge (gateway service)

The edge pipeline publishes CloudEvents to EMQX over MQTT (QoS 1, disk-spooled
locally — see Phase 7 of the implementation sequence). This gateway service
subscribes to `mobisentra/#` and forwards each message to Kafka.

## Topic mapping

**MQTT topics use slashes; Kafka topics use dots.** The gateway maps
`mobisentra/events` → Kafka topic `mobisentra.events` (`/` → `.`).

> ⚠️ Gotcha (found the hard way in Phase 0): MQTT topic matching treats
> `mobisentra/#` and `mobisentra.events` as unrelated — a dot is just a
> character, not a separator. Always publish to `mobisentra/<segment>`
> (slash), never `mobisentra.<segment>`.

## Topology

```text
edge pipeline --MQTT QoS1--> EMQX --subscribe--> bridge (this service) --produce--> Kafka
   mobisentra/events                                              mobisentra.events
```

Delivery is at-least-once. Durability is handled on both sides: edge disk
spool before MQTT publish (Phase 7), consumer-side dedupe after Kafka
(Phase 8). The gateway itself is stateless.

## Why a custom gateway (decision record)

- **EMQX open-source has no Kafka connector.** The Kafka bridge is an
  EMQX Enterprise feature (verified empirically against 5.8.3 and 5.10.3 on
  2026-08-25: the OSS image ships only HTTP + MQTT bridge apps). A custom
  gateway keeps the default stack fully OSI-open.
- **Vehicles have intermittent connectivity**; Kafka-on-vehicle loses data
  during network transitions. MQTT QoS 1 + edge disk spool + server-side
  Kafka is the lossless path. (Full rationale: `Doc/implementation-plan.md` §2.)
- Apache Kafka KRaft instead of Redpanda — Redpanda is BSL.

## Running

Part of the dev stack (`infra/docker-compose.yml` brings it up with
everything else):

```bash
docker compose -f infra/docker-compose.yml up -d
```

Standalone (development):

```bash
cd bridge && pnpm install && pnpm dev
# env: MQTT_URL (default mqtt://localhost:1883)
#      KAFKA_BROKER (default localhost:9092)
#      TOPIC_PREFIX (default mobisentra)
```

## Smoke test

```bash
mosquitto_pub -h localhost -p 1883 -t mobisentra/events \
  -m '{"id":"t1","hello":"mobisentra"}' -q 1

docker compose -f infra/docker-compose.yml exec kafka \
  /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic mobisentra.events \
  --from-beginning --timeout-ms 8000
```

## Hardening (Phase 7.3b — shipped)

- **Backpressure, lossless by construction:** when the Kafka producer goes
  down, the bridge *disconnects* MQTT (fixed `clientId` + `clean: false` →
  persistent session). EMQX queues QoS-1 messages for the offline session
  and flow-controls publishers — nothing is accepted-and-lost. Messages
  already delivered land in a bounded in-memory buffer (oldest dropped +
  counted on overflow — never an OOM; drops are logged with totals).
  Recovery (probe produce succeeding every 2 s) drains the buffer FIFO and
  reconnects MQTT; the session's queued messages then flow through.
  Broker-side queue depth during a long Kafka outage is bounded by EMQX's
  per-session mqueue (default 1000 — tune `max_mqueue` in EMQX for longer
  outages).
- **Counters** every 30 s: `forwarded / dropped / suppressed / buffered / kafka up|down`.
- **Optional pre-Kafka duplicate suppression** (OFF by default): bounded TTL
  cache of CloudEvents ids — `DEDUPE_ENABLED=true`, `DEDUPE_TTL_MS`,
  `DEDUPE_MAX`. The real exactly-once guarantee stays consumer-side
  (Phase 8).

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MQTT_URL` | `mqtt://localhost:1883` | broker url |
| `KAFKA_BROKER` | `localhost:9092` | kafka bootstrap |
| `TOPIC_PREFIX` | `mobisentra` | subscription + mapping prefix |
| `BUFFER_MAX` | `10000` | backpressure buffer bound (messages) |
| `DEDUPE_ENABLED` | `false` | optional pre-Kafka id suppression |
| `DEDUPE_TTL_MS` | `60000` | suppression window |
| `DEDUPE_MAX` | `10000` | suppression cache bound (ids) |
| `COUNTER_INTERVAL_S` | `30` | counters log interval |

### Tests

```bash
cd bridge && pnpm install && pnpm test      # vitest (topics, suppressor, buffer)
pnpm typecheck                              # tsc --noEmit
```
