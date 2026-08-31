/**
 * Backend process assembly (Phase 8, Steps 8.5/8.4b) — the one-process
 * shape from the plan defaults: Kafka consumer + REST API + Socket.IO
 * push in a single Node process (`pnpm dev` locally, compose in 8.4b).
 *
 * Boot order is deliberate: migrations first (idempotent), then the HTTP
 * server, then the consumer supervisor. The consumer restarts with
 * backoff on processor/commit failure (offsets are never committed past
 * a failure — redelivery is safe); SIGTERM/SIGINT run the graceful path:
 * finish in-flight batch → commit → disconnect → close everything.
 */
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { Pool } from "pg";
import { Redis } from "ioredis";
import { createApiServer } from "./api/server.js";
import { PgApiStore, RedisCameraStatus } from "./api/store.js";
import { DedupeService, RedisDedupe } from "./consumer/dedupe.js";
import { EventConsumer, LibrdkafkaDriver } from "./consumer/kafka.js";
import type { ConsumerDriver, ConsumedMessage } from "./consumer/kafka.js";
import { EventPipeline, PgEventStore, RedisLiveState } from "./consumer/pipeline.js";
import { applyMigrations, listMigrations } from "./schema/migrate.js";
import { createPushServer } from "./ws/push.js";

export const BACKEND_VERSION = "0.1.0";

const config = {
  kafkaBroker: process.env.KAFKA_BROKER ?? "localhost:9092",
  kafkaTopic: process.env.KAFKA_TOPIC ?? "mobisentra.events",
  kafkaGroup: process.env.KAFKA_GROUP ?? "mobisentra-backend",
  databaseUrl: process.env.DATABASE_URL ?? "postgres://mobisentra:mobisentra@localhost:5432/mobisentra",
  redisUrl: process.env.REDIS_URL ?? "redis://localhost:6379",
  port: Number(process.env.PORT ?? "3000"),
  host: process.env.HOST ?? "0.0.0.0",
  evidenceRoot: process.env.EVIDENCE_ROOT ?? resolve("../edge/runs/evidence"),
};

function log(message: string): void {
  console.log(`[backend] ${message}`);
}

async function main(): Promise<void> {
  const pool = new Pool({ connectionString: config.databaseUrl });
  const report = await applyMigrations(pool, await listMigrations());
  log(`migrations: applied ${report.applied.length}, skipped ${report.skipped.length}`);

  const redis = new Redis(config.redisUrl);
  const app = createApiServer(new PgApiStore(pool), new RedisCameraStatus(redis), {
    evidenceRoot: config.evidenceRoot,
  });
  await app.listen({ port: config.port, host: config.host });
  const address = app.server.address();
  const port = address !== null && typeof address === "object" ? address.port : config.port;
  const push = createPushServer(app.server);
  log(`http/ws listening on 127.0.0.1:${port} (evidence root: ${config.evidenceRoot})`);

  const pipeline = new EventPipeline(
    new PgEventStore(pool),
    new RedisLiveState(redis),
    new DedupeService(new RedisDedupe(redis)),
    push.pusher
  );

  const processor = {
    async process(message: ConsumedMessage): Promise<void> {
      if (message.value === null) {
        return; // tombstone — commit past it
      }
      let envelope: unknown;
      try {
        envelope = JSON.parse(message.value.toString("utf8"));
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        console.error(`[pipeline] non-JSON message (partition ${message.partition}, offset ${message.offset}): ${detail} — skipping`);
        return;
      }
      const outcome = await pipeline.process(envelope);
      log(`event → ${outcome.kind !== undefined ? `${outcome.kind}/` : ""}${outcome.stage}`);
    },
  };

  let shuttingDown = false;
  let active: { consumer: EventConsumer; driver: ConsumerDriver } | null = null;

  async function supervise(): Promise<void> {
    let backoffMs = 1_000;
    while (!shuttingDown) {
      const driver = await LibrdkafkaDriver.connect({
        broker: config.kafkaBroker,
        groupId: config.kafkaGroup,
        topic: config.kafkaTopic,
      });
      const consumer = new EventConsumer({ driver, processor });
      active = { consumer, driver };
      log(`consumer running (group ${config.kafkaGroup}, topic ${config.kafkaTopic})`);
      try {
        await consumer.run();
        active = null;
        return; // clean stop — EventConsumer.stop() closed the driver
      } catch (err) {
        active = null;
        const detail = err instanceof Error ? err.message : String(err);
        console.error(`[consumer] loop failed (${detail}) — restarting in ${backoffMs}ms (uncommitted offsets replay safely)`);
        await driver.close().catch(() => undefined);
        if (shuttingDown) {
          return;
        }
        await new Promise((sleep) => setTimeout(sleep, backoffMs));
        backoffMs = Math.min(backoffMs * 2, 30_000);
      }
    }
  }

  const supervised = supervise();

  const shutdown = (signal: string): void => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;
    log(`${signal} — graceful shutdown`);
    void (async () => {
      if (active !== null) {
        await active.consumer.stop().catch((err: unknown) => {
          const detail = err instanceof Error ? err.message : String(err);
          console.error(`[consumer] stop error: ${detail}`);
        });
      }
      await supervised;
      await push.close();
      await app.close();
      await pool.end();
      redis.disconnect();
      log("stopped");
      process.exit(0);
    })();
  };
  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));

  await mkdir(config.evidenceRoot, { recursive: true }).catch(() => undefined);
  await supervised;
}

main().catch((err: unknown) => {
  const detail = err instanceof Error ? err.message : String(err);
  console.error(`[backend] fatal: ${detail}`);
  process.exit(1);
});
