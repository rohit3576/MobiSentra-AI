/**
 * MobiSentra MQTT→Kafka gateway (Phase 0, Step 0.8; hardened in Phase 7.3b).
 *
 * Subscribes to `mobisentra/#` on EMQX (QoS 1) and forwards each message to
 * a Kafka topic of the same name, with MQTT `/` mapped to Kafka `.` — the
 * canonical MQTT topic `mobisentra/events` lands on Kafka `mobisentra.events`.
 * Delivery is at-least-once; the real exactly-once guarantee is consumer-side
 * dedupe on the CloudEvents `id` (Phase 8). The gateway is stateless by
 * default (the dedupe cache is opt-in).
 *
 * Backpressure (Phase 7.3b) — the lossless shape given mqtt.js semantics:
 * while the Kafka producer is disconnected, the bridge DISCONNECTS its MQTT
 * client (clean:false, fixed clientId → persistent session): EMQX queues
 * QoS-1 messages for the offline session and flow-controls producers, so
 * nothing is accepted-and-lost. Anything already delivered lands in a
 * bounded buffer (oldest dropped + counted on overflow — never an OOM).
 * Recovery = the periodic probe produce succeeding → buffer drains FIFO →
 * MQTT reconnects and resubscribes → the queued messages flow through.
 *
 * Env config (defaults in parentheses):
 *   MQTT_URL (mqtt://localhost:1883) — broker url
 *   KAFKA_BROKER (localhost:9092)    — kafka bootstrap
 *   TOPIC_PREFIX (mobisentra)        — subscription + mapping prefix
 *   BUFFER_MAX (10000)               — backpressure buffer bound (messages)
 *   DEDUPE_ENABLED (false)           — optional pre-Kafka id suppression
 *   DEDUPE_TTL_MS (60000)            — suppression window
 *   DEDUPE_MAX (10000)               — suppression cache bound (ids)
 *   COUNTER_INTERVAL_S (30)          — periodic counters log
 */
import process from "node:process";
import mqtt from "mqtt";
import { RdKafka } from "@confluentinc/kafka-javascript";
import { createBoundedBuffer } from "./lib/buffer.js";
import { createSuppressor } from "./lib/suppress.js";
import { mapTopic } from "./lib/topics.js";

const MQTT_URL = process.env.MQTT_URL ?? "mqtt://localhost:1883";
const KAFKA_BROKER = process.env.KAFKA_BROKER ?? "localhost:9092";
const TOPIC_PREFIX = process.env.TOPIC_PREFIX ?? "mobisentra";
const BUFFER_MAX = envInt("BUFFER_MAX", 10_000);
const DEDUPE_ENABLED = envBool("DEDUPE_ENABLED", false);
const DEDUPE_TTL_MS = envInt("DEDUPE_TTL_MS", 60_000);
const DEDUPE_MAX = envInt("DEDUPE_MAX", 10_000);
const COUNTER_INTERVAL_S = envInt("COUNTER_INTERVAL_S", 30);
const RECOVERY_PROBE_S = 2;

function envInt(name: string, fallback: number): number {
  const raw = process.env[name];
  const value = raw === undefined ? NaN : Number(raw);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function envBool(name: string, fallback: boolean): boolean {
  const raw = process.env[name];
  if (raw === undefined) return fallback;
  return raw === "1" || raw.toLowerCase() === "true";
}

interface Queued {
  kafkaTopic: string;
  payload: Buffer;
}

const producer = new RdKafka.Producer({
  "bootstrap.servers": KAFKA_BROKER,
  dr_cb: true,
});

const suppressor = createSuppressor({ enabled: DEDUPE_ENABLED, max: DEDUPE_MAX, ttlMs: DEDUPE_TTL_MS });
const buffer = createBoundedBuffer<Queued>(BUFFER_MAX);

let forwarded = 0;
let kafkaUp = true;
let mqttClient = connectMqtt();

function connectMqtt(): mqtt.MqttClient {
  const client = mqtt.connect(MQTT_URL, {
    clientId: "mobisentra-bridge", // fixed id + clean:false = persistent session
    clean: false,
    reconnectPeriod: 2000,
  });
  client.on("connect", () => {
    console.log(`[bridge] mqtt connected: ${MQTT_URL}`);
    client.subscribe(`${TOPIC_PREFIX}/#`, { qos: 1 }, (err) => {
      if (err) {
        console.error(`[bridge] subscribe failed: ${err.message}`);
      }
    });
  });
  client.on("message", onMessage);
  client.on("error", (err) => console.error(`[bridge] mqtt error: ${err.message}`));
  return client;
}

function onMessage(topic: string, payload: Buffer): void {
  const kafkaTopic = mapTopic(topic, TOPIC_PREFIX);
  if (kafkaTopic === null) {
    return;
  }
  if (suppressor.shouldSuppress(payload)) {
    return;
  }
  if (kafkaUp && forward(kafkaTopic, payload)) {
    return;
  }
  if (!buffer.push({ kafkaTopic, payload })) {
    console.error(
      `[bridge] buffer overflow (${BUFFER_MAX}) — oldest dropped, dropped total ${buffer.droppedCount}`
    );
  }
}

function forward(kafkaTopic: string, payload: Buffer): boolean {
  try {
    producer.produce(kafkaTopic, null, payload, "mobisentra-bridge");
    forwarded += 1;
    return true;
  } catch (err) {
    console.error(`[bridge] kafka produce failed for ${kafkaTopic}:`, err);
    return false;
  }
}

function drainBuffer(): void {
  while (buffer.size > 0) {
    const head = buffer.peek();
    if (head === undefined || !forward(head.kafkaTopic, head.payload)) {
      return;
    }
    buffer.pop();
  }
}

function loseKafka(reason: string): void {
  if (kafkaUp) {
    kafkaUp = false;
    mqttClient.end(true); // disconnect: EMQX queues QoS-1 for the persistent session
    console.error(`[bridge] kafka down (${reason}) — mqtt disconnected, buffering (max ${BUFFER_MAX})`);
  }
}

function recoverKafka(trigger: string): void {
  if (kafkaUp) {
    return;
  }
  kafkaUp = true;
  drainBuffer();
  mqttClient = connectMqtt();
  console.log(`[bridge] kafka back (${trigger}) — buffer drained, mqtt reconnecting`);
}

function connectProducer(): Promise<RdKafka.Metadata> {
  return new Promise((resolve, reject) => {
    producer.connect({}, (err, metadata) => (err ? reject(err) : resolve(metadata)));
  });
}

function flush(timeoutMs: number = 10_000): Promise<void> {
  return new Promise((resolve) => producer.flush(timeoutMs, () => resolve()));
}

async function main(): Promise<void> {
  const metadata = await connectProducer();
  console.log(
    `[bridge] kafka producer connected: ${KAFKA_BROKER} (${metadata.brokers.length} broker(s))`
  );
  producer.setPollInterval(100);
  console.log(`[bridge] mqtt connecting: ${MQTT_URL} (topic ${TOPIC_PREFIX}/#)`);
}

const DOWN_ERROR_PATTERN = /all broker connections are down|broker transport failure|connection refused/i;

producer.on("event.error", (err) => {
  if (err.code === -195 /* ALL_BROKERS_DOWN */ || err.isFatal || DOWN_ERROR_PATTERN.test(err.message)) {
    loseKafka(err.message);
  } else {
    console.error(`[bridge] kafka error: ${err.message}`);
  }
});
producer.on("connection.failure", (err) => loseKafka(err.message));

// Recovery must NOT depend on the buffer being non-empty: in the
// MQTT-disconnected epoch messages queue at the broker, our buffer stays
// empty, and a message-driven probe would deadlock. A watermark query is a
// real broker round-trip — its success means Kafka is back.
const recoveryProbe = setInterval(() => {
  if (kafkaUp) {
    return;
  }
  producer.queryWatermarkOffsets("mobisentra.events", 0, 1500, (err) => {
    if (err === undefined || err === null) {
      recoverKafka("watermark probe");
    }
  });
}, RECOVERY_PROBE_S * 1000);

const countersLog = setInterval(() => {
  console.log(
    `[bridge] counters: forwarded=${forwarded} dropped=${buffer.droppedCount} ` +
      `suppressed=${suppressor.suppressedCount} buffered=${buffer.size} ` +
      `kafka=${kafkaUp ? "up" : "down"}`
  );
}, COUNTER_INTERVAL_S * 1000);

countersLog.unref();
recoveryProbe.unref();

async function shutdown(): Promise<void> {
  console.log("[bridge] shutting down");
  clearInterval(countersLog);
  clearInterval(recoveryProbe);
  drainBuffer();
  mqttClient.end(false);
  await flush();
  producer.disconnect();
  process.exit(0);
}

process.on("SIGINT", () => void shutdown());
process.on("SIGTERM", () => void shutdown());

void main();
