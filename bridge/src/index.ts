/**
 * MobiSentra MQTT→Kafka gateway (Phase 0, Step 0.8; hardened in Phase 7).
 *
 * Subscribes to `mobisentra/#` on EMQX (QoS 1) and produces each message to
 * a Kafka topic of the same name, with MQTT `/` mapped to Kafka `.` — the
 * canonical MQTT topic `mobisentra/events` lands on Kafka `mobisentra.events`.
 * Stateless forwarder: durability is handled on both sides (edge disk spool
 * before MQTT publish; consumer-side dedupe after Kafka). Delivery is
 * at-least-once.
 *
 * Exists because EMQX open-source (5.8+) ships no Kafka connector — that is
 * an Enterprise feature. This gateway keeps the default stack fully open.
 */
import process from "node:process";
import mqtt from "mqtt";
import { RdKafka } from "@confluentinc/kafka-javascript";
import { mapTopic } from "./lib/topics.js";

const MQTT_URL = process.env.MQTT_URL ?? "mqtt://localhost:1883";
const KAFKA_BROKER = process.env.KAFKA_BROKER ?? "localhost:9092";
const TOPIC_PREFIX = process.env.TOPIC_PREFIX ?? "mobisentra";

const producer = new RdKafka.Producer({
  "bootstrap.servers": KAFKA_BROKER,
  dr_cb: true,
});

const mqttClient = mqtt.connect(MQTT_URL, {
  clientId: "mobisentra-bridge",
  clean: true,
  reconnectPeriod: 2000,
});

let forwarded = 0;

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

  await new Promise<void>((resolve, reject) => {
    mqttClient.subscribe(`${TOPIC_PREFIX}/#`, { qos: 1 }, (err) =>
      err ? reject(err) : resolve()
    );
  });
  console.log(`[bridge] subscribed: ${TOPIC_PREFIX}/#`);

  mqttClient.on("message", (topic, payload) => {
    const kafkaTopic = mapTopic(topic, TOPIC_PREFIX);
    if (kafkaTopic === null) return;
    try {
      producer.produce(kafkaTopic, null, payload, "mobisentra-bridge");
      forwarded += 1;
      console.log(`[bridge] ${topic} -> kafka:${kafkaTopic} (${forwarded} total)`);
    } catch (err) {
      console.error(`[bridge] kafka produce failed for ${topic}:`, err);
    }
  });
}

producer.on("event.error", (err) => console.error(`[bridge] kafka error: ${err.message}`));
mqttClient.on("connect", () => console.log(`[bridge] mqtt connected: ${MQTT_URL}`));
mqttClient.on("error", (err) => console.error(`[bridge] mqtt error: ${err.message}`));

async function shutdown(): Promise<void> {
  console.log("[bridge] shutting down");
  mqttClient.end(false);
  await flush();
  producer.disconnect();
  process.exit(0);
}

process.on("SIGINT", () => void shutdown());
process.on("SIGTERM", () => void shutdown());

void main();
