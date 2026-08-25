# MobiSentra Dev Stack

One command brings up the full backend infrastructure — no cameras, no cloud, no API keys:

```bash
docker compose -f infra/docker-compose.yml up -d
```

## Services

| Service | Image | Host port | Container port | Purpose |
|---|---|---|---|---|
| Kafka | `apache/kafka:4.0.0` (KRaft, no ZooKeeper) | 9092 | 9092 + 29092 (internal) | Server-side event backbone |
| EMQX | `emqx/emqx:5.10.3` | 1883 / 18083 | same | MQTT broker (edge messaging) |
| bridge | built from `../bridge` | — | — | MQTT→Kafka gateway (EMQX OSS has no Kafka connector) |
| PostgreSQL | `postgres:16` | 5432 | 5432 | Events / incidents / cameras / audit history |
| Redis | `redis:7-alpine` | 6379 | 6379 | Live state (latest per camera/vehicle, dedupe) |
| MLflow | `ghcr.io/mlflow/mlflow:v3.1.0` | 5001 | 5000 | Experiment tracking + model registry |

## Access

- **EMQX dashboard:** http://localhost:18083 — `admin` / `mobisentra-dev` (dev default, set in compose; change for anything exposed)
- **MLflow UI:** http://localhost:5001 (host port 5001 — macOS AirPlay occupies 5000)
- **PostgreSQL:** `localhost:5432`, user/pass/db `mobisentra` (dev defaults)
- **Kafka (host tooling):** `localhost:9092` · **Kafka (from containers):** `kafka:29092`

## Kafka topics

MobiSentra topics (auto-created; create explicitly for prod-like setups):

```bash
docker compose -f infra/docker-compose.yml exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --create --topic mobisentra.events --partitions 1 --replication-factor 1
```

Topics: `mobisentra.detection`, `mobisentra.events`, `mobisentra.alerts`, `mobisentra.analytics`.

## MQTT → Kafka smoke test (Phase 0, Step 0.8)

The bridge gateway starts automatically with the stack (topic mapping:
MQTT `mobisentra/events` → Kafka `mobisentra.events` — see
[`../bridge/README.md`](../bridge/README.md)).

1. Bring the stack up and wait for health: `docker compose -f infra/docker-compose.yml ps`
2. Publish a test message from the edge environment:
   ```bash
   cd ../edge && uv run python -c "
   import paho.mqtt.client as mqtt
   import time, json
   c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
   c.connect('localhost', 1883)
   c.loop_start()
   time.sleep(1)
   c.publish('mobisentra/events', json.dumps({'id': 't1', 'hello': 'mobisentra'}), qos=1)
   time.sleep(1)
   c.loop_stop()
   "
   ```
3. Consume it back from Kafka:
   ```bash
   docker compose -f infra/docker-compose.yml exec kafka \
     /opt/kafka/bin/kafka-console-consumer.sh \
     --bootstrap-server localhost:9092 --topic mobisentra.events \
     --from-beginning --timeout-ms 8000
   ```

## Teardown

```bash
docker compose -f infra/docker-compose.yml down       # stop (keep data)
docker compose -f infra/docker-compose.yml down -v    # stop and wipe data
```
