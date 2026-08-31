/**
 * Integration-suite plumbing (Phase 8, Step 8.5) — the gated live-stack
 * pattern: every resource here talks to the REAL compose services, so
 * the suite skips with instructions when the stack is down and never
 * touches anything it didn't prefix itself (`it8-…` ids/keys/cameras).
 */
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import net from "node:net";
import { io } from "socket.io-client";
import type { Socket } from "socket.io-client";
import { RdKafka } from "@confluentinc/kafka-javascript";
import { Pool } from "pg";
import { Redis } from "ioredis";

export const KAFKA_TOPIC = "mobisentra.events";
export const runPrefix = `it8-${Date.now().toString(36)}-`;
export const backendRoot = resolve(fileURLToPath(import.meta.url), "../../..");

function reachable(port: number, host = "127.0.0.1", timeoutMs = 2_500): Promise<boolean> {
  return new Promise((resolvePromise) => {
    const socket = net.connect({ port, host });
    const done = (ok: boolean): void => {
      socket.destroy();
      resolvePromise(ok);
    };
    socket.setTimeout(timeoutMs, () => done(false));
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
  });
}

export async function stackUp(): Promise<boolean> {
  // two attempts: right after a heavy integration run the broker can be
  // slow to accept TCP while its coordinator settles — that is churn, not
  // "stack down", and must not flip the suite into a false skip
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const [kafka, pg, redis] = await Promise.all([reachable(9092), reachable(5432), reachable(6379)]);
    if (kafka && pg && redis) {
      return true;
    }
    if (attempt === 0) {
      await new Promise((sleep) => setTimeout(sleep, 3_000));
    }
  }
  return false;
}

export async function testPool(): Promise<Pool> {
  return new Pool({ connectionString: "postgres://mobisentra:mobisentra@localhost:5432/mobisentra" });
}

export function testRedis(): Redis {
  return new Redis("redis://localhost:6379");
}

/** Publishes envelopes straight to Kafka (flushed = broker-acknowledged). */
export class Publisher {
  private constructor(private readonly producer: RdKafka.Producer) {}

  static connect(broker = "localhost:9092"): Promise<Publisher> {
    return new Promise((resolveConnect, rejectConnect) => {
      const producer = new RdKafka.Producer({
        "bootstrap.servers": broker,
        "queue.buffering.max.ms": 5,
        dr_cb: true,
      });
      producer.connect({ timeout: 10_000 }, (err) => {
        if (err) {
          rejectConnect(new Error(`kafka producer connect failed: ${err.message}`));
          return;
        }
        producer.on("event.error", (error) => {
          console.error(`[it-publisher] ${error.message}`);
        });
        resolveConnect(new Publisher(producer));
      });
    });
  }

  async publish(envelopes: Array<{ key?: string; payload: string }>): Promise<void> {
    for (const item of envelopes) {
      this.producer.produce(KAFKA_TOPIC, -1, Buffer.from(item.payload, "utf8"), item.key ?? null);
    }
    await new Promise<void>((resolveFlush, rejectFlush) => {
      this.producer.flush(10_000, (err) => (err ? rejectFlush(new Error(`kafka flush failed: ${err.message}`)) : resolveFlush()));
    });
  }

  close(): Promise<void> {
    return new Promise((resolveClose) => {
      this.producer.disconnect((err) => {
        if (err) {
          console.error(`[it-publisher] disconnect: ${err.message}`);
        }
        resolveClose();
      });
    });
  }
}

export interface BackendHandle {
  baseUrl: string;
  kill(): Promise<void>;
  stop(): Promise<void>;
}

/**
 * Spawns the assembled backend (`src/index.ts`) with PORT=0 and waits for
 * its ready line. Stdout is drained continuously (ring buffer) so a busy
 * consumer can never block on a full pipe.
 */
export async function startBackend(): Promise<BackendHandle> {
  const evidenceRoot = await mkdtemp(join(tmpdir(), "mobisentra-it-evidence-"));
  // detached = own process group: SIGKILL must reach the tsx grandchild
  // too, or an orphaned consumer silently keeps owning the group's
  // partition (live-caught — it poisoned every WS-receipt assertion)
  const child: ChildProcess = spawn("pnpm", ["exec", "tsx", "src/index.ts"], {
    cwd: backendRoot,
    detached: true,
    env: {
      ...process.env,
      PORT: "0",
      HOST: "127.0.0.1",
      KAFKA_BROKER: "localhost:9092",
      DATABASE_URL: "postgres://mobisentra:mobisentra@localhost:5432/mobisentra",
      REDIS_URL: "redis://localhost:6379",
      EVIDENCE_ROOT: evidenceRoot,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  const tail: string[] = [];
  const drain = (chunk: Buffer): void => {
    const lines = chunk.toString("utf8").split("\n");
    for (const line of lines) {
      if (line.trim().length > 0) {
        tail.push(line);
        if (tail.length > 200) {
          tail.shift();
        }
      }
    }
  };
  child.stdout?.on("data", drain);
  child.stderr?.on("data", drain);

  const exited = new Promise<void>((resolveExit) => {
    child.once("exit", () => resolveExit());
  });

  const baseUrl = await new Promise<string>((resolveReady, rejectReady) => {
    const timer = setTimeout(() => {
      rejectReady(new Error(`backend not ready in 60s — last output:\n${tail.join("\n")}`));
    }, 60_000);
    const scan = (chunk: Buffer): void => {
      const match = /http\/ws listening on 127\.0\.0\.1:(\d+)/.exec(chunk.toString("utf8"));
      if (match !== null) {
        clearTimeout(timer);
        resolveReady(`http://127.0.0.1:${match[1]}`);
      }
    };
    child.stdout?.on("data", scan);
    exited.then(() => {
      clearTimeout(timer);
      rejectReady(new Error(`backend exited before ready — last output:\n${tail.join("\n")}`));
    });
  });

  const waitForExit = async (): Promise<void> => {
    if (child.exitCode !== null) {
      return;
    }
    await exited;
  };

  return {
    baseUrl,
    kill: async () => {
      if (child.pid !== undefined) {
        try {
          process.kill(-child.pid, "SIGKILL"); // whole process group
        } catch {
          // group already gone
        }
      }
      await waitForExit();
    },
    stop: async () => {
      // signal the GROUP: pnpm does not reliably forward SIGTERM to the
      // tsx child (live-caught — every stop() leaked an orphaned consumer)
      if (child.pid !== undefined) {
        try {
          process.kill(-child.pid, "SIGTERM");
        } catch {
          // group already gone
        }
      }
      const force = setTimeout(() => {
        try {
          if (child.pid !== undefined) {
            process.kill(-child.pid, "SIGKILL");
          }
        } catch {
          // group already gone
        }
      }, 8_000);
      await waitForExit();
      clearTimeout(force);
      try {
        if (child.pid !== undefined) {
          process.kill(-child.pid, "SIGKILL"); // defensive reap — no-op if clean
        }
      } catch {
        // group already reaped
      }
    },
  };
}

/** Socket.IO collector: subscribes a vehicle room and re-subscribes after
 * every reconnect (the crash-restart test needs receipts across restarts). */
export class ReceiptCollector {
  readonly arrivals = new Map<string, number>();
  private readonly socket: Socket;

  private constructor(socket: Socket) {
    this.socket = socket;
  }

  static connect(wsUrl: string, vehicleId: string): Promise<ReceiptCollector> {
    const socket = io(wsUrl, { transports: ["websocket"] });
    const collector = new ReceiptCollector(socket);
    socket.on("event", (payload: { id?: unknown }) => {
      if (typeof payload?.id === "string") {
        collector.arrivals.set(payload.id, Date.now());
      }
    });
    return new Promise((resolveReady, rejectReady) => {
      const timer = setTimeout(() => rejectReady(new Error("collector subscribe timed out")), 20_000);
      const subscribe = (): void => {
        socket.emit("subscribe", vehicleId, (joined: boolean) => {
          if (joined) {
            clearTimeout(timer);
            resolveReady(collector);
          }
        });
      };
      socket.on("connect", subscribe); // first connect AND every reconnect
    });
  }

  async waitFor(predicate: () => boolean, timeoutMs: number, what: string): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    while (!predicate()) {
      if (Date.now() > deadline) {
        throw new Error(`timed out waiting for ${what} (have ${this.arrivals.size})`);
      }
      await new Promise((sleep) => setTimeout(sleep, 25));
    }
  }

  close(): void {
    this.socket.disconnect();
  }
}

/** Deletes only rows this run prefixed — audit first (FK), then events. */
export async function cleanupRun(pool: Pool, redis: Redis): Promise<void> {
  await pool.query("DELETE FROM audit_log WHERE event_id LIKE $1", [`${runPrefix}%`]);
  await pool.query("DELETE FROM events WHERE event_id LIKE $1", [`${runPrefix}%`]);
  for (const pattern of [`camera:${runPrefix}*`, `dedupe:/mobisentra/edge/${runPrefix}*`]) {
    const stream = redis.scanStream({ match: pattern, count: 100 });
    stream.on("data", (keys: string[]) => {
      if (keys.length > 0) {
        void redis.del(...keys);
      }
    });
    await new Promise<void>((resolveStream) => stream.once("end", () => resolveStream()));
  }
}
