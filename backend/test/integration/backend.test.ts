/**
 * Gate-8 integration suite (Phase 8, Step 8.5) — runs against the live
 * compose stack, gated on reachability (skip-with-instructions, the edge
 * pattern). Everything this suite creates carries the run prefix and is
 * deleted in `afterAll` — no shared-table assertions without filters.
 *
 * The four Gate-8 criteria, in order: three-store landing, < 1 s
 * publish→WS-receipt latency, ack/escalate audit rows, and crash-restart
 * mid-stream with zero lost/duplicated PG rows.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  cleanupRun,
  Publisher,
  ReceiptCollector,
  runPrefix,
  stackUp,
  startBackend,
  testPool,
  testRedis,
} from "./helpers.js";
import type { BackendHandle } from "./helpers.js";
import type { Pool } from "pg";
import type Redis from "ioredis";

const VEHICLE = `${runPrefix}BUS`;
const CAMERA = `${runPrefix}CAM1`;
const SOURCE = `/mobisentra/edge/${VEHICLE}/${CAMERA}`;
const now = (): string => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

function envelope(id: string): string {
  return JSON.stringify({
    specversion: "1.0",
    id,
    source: SOURCE,
    type: "org.mobisentra.event.fall_detected",
    time: now(),
    datacontenttype: "application/json",
    data: {
      event_type: "fall_detected",
      severity: "HIGH",
      camera_id: CAMERA,
      timestamp: now(),
      confidence: 0.9,
      tracks: [7],
      model_versions: { detector: "it@1" },
    },
  });
}

const ids = (label: string, from: number, to: number): string[] => {
  const list: string[] = [];
  for (let index = from; index < to; index += 1) {
    list.push(`${runPrefix}${label}${String(index).padStart(4, "0")}`);
  }
  return list;
};

const stackAvailable = await stackUp();
const suite = stackAvailable ? describe : describe.skip;
if (!stackAvailable) {
  console.warn(
    "[integration] SKIPPED — dev stack unreachable.\n" +
      "  Start it with: docker compose -f infra/docker-compose.yml up -d\n" +
      "  (needs kafka :9092, postgres :5432, redis :6379)"
  );
}

suite("backend integration (live compose stack)", () => {
  let backend: BackendHandle;
  let publisher: Publisher;
  let pool: Pool;
  let redis: Redis;
  let collector: ReceiptCollector;

  beforeAll(async () => {
    pool = await testPool();
    redis = testRedis();
    backend = await startBackend();
    publisher = await Publisher.connect();
    collector = await ReceiptCollector.connect(backend.baseUrl, VEHICLE);

    // readiness gate: group rebalance after broker restarts/member churn
    // can take seconds — don't measure anything until the partition is
    // actually held, the pipeline ran, and a push round-tripped
    const warmupId = `${runPrefix}warmup`;
    await publisher.publish([{ payload: envelope(warmupId) }]);
    await collector.waitFor(() => collector.arrivals.has(warmupId), 90_000, "warmup receipt (group rebalance)");
  }, 150_000);

  afterAll(async () => {
    await cleanupRun(pool, redis);
    collector?.close();
    await publisher?.close();
    await backend?.stop();
    await pool?.end();
    redis?.disconnect();
  }, 60_000);

  it(
    "one published event lands in ALL THREE stores",
    async () => {
      const batch = ids("a", 0, 10);
      await publisher.publish(batch.map((id) => ({ payload: envelope(id) })));
      await collector.waitFor(() => batch.every((id) => collector.arrivals.has(id)), 30_000, "10 WS receipts");

      const rows = await pool.query("SELECT event_id FROM events WHERE event_id LIKE $1", [`${runPrefix}a%`]);
      expect(rows.rows.map((row: { event_id: string }) => row.event_id).sort()).toEqual([...batch].sort());

      const hash = await redis.hgetall(`camera:${CAMERA}`);
      expect(hash["last_event_type"]).toBe("fall_detected");
      expect(hash["severity"]).toBe("HIGH");
      expect(typeof hash["ts"]).toBe("string");
      expect(await redis.ttl(`camera:${CAMERA}`)).toBeGreaterThan(0);
    },
    60_000
  );

  it(
    "latency probe: publish → WS receipt < 1 s (the Gate-8 criterion)",
    async () => {
      const probes = ids("l", 0, 3);
      const sentAt = Date.now();
      await publisher.publish(probes.map((id) => ({ payload: envelope(id) })));
      await collector.waitFor(() => probes.every((id) => collector.arrivals.has(id)), 30_000, "3 latency probes");

      const worstMs = Math.max(...probes.map((id) => (collector.arrivals.get(id) ?? sentAt) - sentAt));
      console.log(`[integration] latency probe: worst publish→WS-receipt = ${worstMs} ms (criterion < 1000 ms)`);
      expect(worstMs).toBeLessThan(1_000);
    },
    60_000
  );

  it(
    "ack + escalate write their audit rows",
    async () => {
      const target = `${runPrefix}a0000`;
      const ack = await fetch(`${backend.baseUrl}/api/incidents/${target}/ack`, {
        method: "POST",
        body: JSON.stringify({ actor: "it-harness" }),
        headers: { "content-type": "application/json" },
      });
      expect(ack.status).toBe(200);
      const escalate = await fetch(`${backend.baseUrl}/api/incidents/${target}/escalate`, {
        method: "POST",
        body: JSON.stringify({ actor: "it-harness", detail: { why: "gate-8" } }),
        headers: { "content-type": "application/json" },
      });
      expect(escalate.status).toBe(200);

      const audit = await pool.query(
        "SELECT action, actor FROM audit_log WHERE event_id = $1 ORDER BY id",
        [target]
      );
      expect(audit.rows).toEqual([
        { action: "ack", actor: "it-harness" },
        { action: "escalate", actor: "it-harness" },
      ]);
      const row = await pool.query(
        "SELECT acked_at, acked_by, escalation->>'actor' AS esc_actor FROM events WHERE event_id = $1",
        [target]
      );
      expect(row.rows[0]?.acked_at).not.toBeNull();
      expect(row.rows[0]?.acked_by).toBe("it-harness");
      expect(row.rows[0]?.esc_actor).toBe("it-harness");
    },
    60_000
  );

  it(
    "crash-restart mid-stream: zero lost, zero duplicated PG rows",
    async () => {
      await backend.stop(); // only one consumer may hold the group's single partition

      const first = await startBackend();
      const beforeKill = await ReceiptCollector.connect(first.baseUrl, VEHICLE);
      const batch = ids("r", 0, 150);
      await publisher.publish(batch.map((id) => ({ payload: envelope(id) })));

      const restartPrefix = `${runPrefix}r%`;
      let seen = 0;
      const killDeadline = Date.now() + 30_000;
      while (seen < 1) {
        const count = await pool.query("SELECT count(*)::int AS n FROM events WHERE event_id LIKE $1", [restartPrefix]);
        seen = count.rows[0]?.n ?? 0;
        if (Date.now() > killDeadline) {
          throw new Error("no rows landed before the kill window");
        }
        await new Promise((sleep) => setTimeout(sleep, 10));
      }
      await first.kill(); // SIGKILL mid-stream — uncommitted offsets stay uncommitted
      const killedAt = (
        await pool.query("SELECT count(*)::int AS n FROM events WHERE event_id LIKE $1", [restartPrefix])
      ).rows[0]?.n;
      beforeKill.close();

      const revived = await startBackend();
      const afterRestart = await ReceiptCollector.connect(revived.baseUrl, VEHICLE);
      const settleDeadline = Date.now() + 120_000; // group rebalance headroom
      for (;;) {
        const count = await pool.query("SELECT count(*)::int AS n FROM events WHERE event_id LIKE $1", [restartPrefix]);
        if ((count.rows[0]?.n ?? 0) === 150) {
          break;
        }
        if (Date.now() > settleDeadline) {
          throw new Error("rows did not settle at 150 after restart");
        }
        await new Promise((sleep) => setTimeout(sleep, 50));
      }

      const finalCount = await pool.query(
        "SELECT count(*)::int AS n, count(DISTINCT event_id)::int AS d FROM events WHERE event_id LIKE $1",
        [restartPrefix]
      );
      const landedIds = (
        await pool.query("SELECT event_id FROM events WHERE event_id LIKE $1 ORDER BY event_id", [restartPrefix])
      ).rows.map((row: { event_id: string }) => row.event_id);

      const receiptCounts = new Map<string, number>();
      for (const id of [...beforeKill.arrivals.keys(), ...afterRestart.arrivals.keys()]) {
        receiptCounts.set(id, (receiptCounts.get(id) ?? 0) + 1);
      }
      const doublePushed = [...receiptCounts.entries()].filter(([, times]) => times > 1);

      console.log(
        `[integration] restart verdict: killed at ${killedAt}/150 · final rows=${finalCount.rows[0]?.n} distinct=${finalCount.rows[0]?.d} · receipts before=${beforeKill.arrivals.size} after=${afterRestart.arrivals.size} double-pushed=${doublePushed.length}`
      );

      expect(killedAt ?? 0).toBeLessThan(150); // the crash really was mid-stream
      expect(finalCount.rows[0]?.n).toBe(150); // zero lost
      expect(finalCount.rows[0]?.d).toBe(150); // zero duplicated
      expect(landedIds).toEqual([...batch].sort());
      expect(doublePushed).toEqual([]); // redeliveries never re-emit

      afterRestart.close();
      await revived.stop();
    },
    200_000
  );
});
