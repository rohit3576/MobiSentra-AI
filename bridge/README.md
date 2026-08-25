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

## Hardening (Phase 7 backlog)

- Pause MQTT consumption while Kafka producer is disconnected (backpressure)
- Metrics: forwarded/dropped/failed counters → Prometheus
- Duplicate suppression option at the gateway (pre-Kafka)
