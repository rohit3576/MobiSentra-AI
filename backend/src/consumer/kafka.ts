/**
 * Kafka consumer wrapper (Phase 8, Step 8.1b).
 *
 * Losslessness policy in one line: **offsets are committed only after the
 * processor has resolved successfully for the whole batch** — anything
 * else is at-least-once redelivery, collapsed downstream by the Redis
 * dedupe (8.2) and PG `ON CONFLICT` (8.3a). A processor failure stops the
 * loop with zero commits past the failure point (no silent skips).
 *
 * Layering mirrors the edge publisher's Transport split: the loop and its
 * policy live in `EventConsumer` over the minimal `ConsumerDriver`
 * protocol (unit-tested with fakes); the librdkafka adapter
 * (`LibrdkafkaDriver`) is thin and live-tested in Step 8.5. Processing is
 * deliberately sequential (bounded concurrency = 1) — per-partition order
 * is preserved and MVP event rates need nothing more.
 */
import { RdKafka } from "@confluentinc/kafka-javascript";

export interface ConsumedMessage {
  topic: string;
  partition: number;
  offset: bigint;
  value: Buffer | null;
}

export interface MessageProcessor {
  process(message: ConsumedMessage): Promise<void>;
}

export interface ConsumerDriver {
  /** Next batch (empty when idle). Rejects on transport errors. */
  fetch(timeoutMs: number): Promise<ConsumedMessage[]>;
  /** Persist the consumed-through position for the batch's partitions. */
  commit(messages: ConsumedMessage[]): Promise<void>;
  close(): Promise<void>;
}

export interface EventConsumerOptions {
  driver: ConsumerDriver;
  processor: MessageProcessor;
  fetchTimeoutMs?: number;
}

const DEFAULT_FETCH_TIMEOUT_MS = 250;

export class EventConsumer {
  private readonly driver: ConsumerDriver;
  private readonly processor: MessageProcessor;
  private readonly fetchTimeoutMs: number;
  private running = true;
  private loopExited: Promise<void> | null = null;

  constructor(options: EventConsumerOptions) {
    this.driver = options.driver;
    this.processor = options.processor;
    this.fetchTimeoutMs = options.fetchTimeoutMs ?? DEFAULT_FETCH_TIMEOUT_MS;
  }

  /** Consume until `stop()`. Rejects on processor or commit failure — the
   * caller (supervisor) decides restart-with-backoff; no offsets were
   * committed past the failure, so a restart replays safely. */
  run(): Promise<void> {
    this.loopExited = this.loop();
    return this.loopExited;
  }

  private async loop(): Promise<void> {
    while (this.running) {
      const batch = await this.driver.fetch(this.fetchTimeoutMs);
      if (batch.length === 0) {
        continue;
      }
      for (const message of batch) {
        await this.processor.process(message);
      }
      await this.driver.commit(batch);
    }
  }

  /** Graceful shutdown: stop fetching, let the in-flight batch finish and
   * commit, then close the driver. */
  async stop(): Promise<void> {
    this.running = false;
    await this.loopExited;
    await this.driver.close();
  }
}

export interface LibrdkafkaDriverOptions {
  broker: string;
  groupId: string;
  topic: string;
}

/** librdkafka adapter: manual commits (`enable.auto.commit: false`),
 * flow-mode consumption into an internal queue, per-partition max offset
 * + 1 committed per batch. Live-tested in Step 8.5. */
export class LibrdkafkaDriver implements ConsumerDriver {
  private readonly consumer: RdKafka.KafkaConsumer;
  private queue: ConsumedMessage[] = [];
  private transportError: Error | null = null;

  private constructor(consumer: RdKafka.KafkaConsumer) {
    this.consumer = consumer;
  }

  static connect(options: LibrdkafkaDriverOptions): Promise<LibrdkafkaDriver> {
    const consumer = new RdKafka.KafkaConsumer(
      {
        "bootstrap.servers": options.broker,
        "group.id": options.groupId,
        "enable.auto.commit": false,
      },
      { "auto.offset.reset": "earliest" }
    );
    return new Promise<LibrdkafkaDriver>((resolve, reject) => {
      consumer.connect({ timeout: 10_000 }, (err) => {
        if (err) {
          reject(new Error(`kafka consumer connect failed: ${err.message}`));
          return;
        }
        const driver = new LibrdkafkaDriver(consumer);
        consumer.subscribe([options.topic]);
        consumer.on("offset.commit", (error) => {
          // commits are fire-and-forget in librdkafka; failures land here
          if (error) {
            driver.transportError = new Error(`kafka commit failed: ${error.message}`);
          }
        });
        consumer.consume((error, messages) => {
          if (error) {
            driver.transportError = new Error(`kafka consume failed: ${error.message}`);
            return;
          }
          for (const message of messages) {
            driver.queue.push({
              topic: message.topic,
              partition: message.partition,
              offset: BigInt(message.offset),
              value: message.value,
            });
          }
        });
        resolve(driver);
      });
    });
  }

  async fetch(timeoutMs: number): Promise<ConsumedMessage[]> {
    const deadline = Date.now() + timeoutMs;
    while (this.queue.length === 0) {
      if (this.transportError !== null) {
        const error = this.transportError;
        this.transportError = null;
        throw error;
      }
      if (Date.now() >= deadline) {
        return [];
      }
      await sleep(25);
    }
    const batch = this.queue;
    this.queue = [];
    return batch;
  }

  async commit(messages: ConsumedMessage[]): Promise<void> {
    const highest = new Map<string, { topic: string; partition: number; offset: bigint }>();
    for (const message of messages) {
      const key = `${message.topic}:${message.partition}`;
      const current = highest.get(key);
      if (current === undefined || message.offset > current.offset) {
        highest.set(key, message);
      }
    }
    const positions = [...highest.values()].map((entry) => ({
      topic: entry.topic,
      partition: entry.partition,
      offset: Number(entry.offset + 1n), // commit = resume-from offset
    }));
    try {
      this.consumer.commit(positions);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      throw new Error(`kafka commit failed: ${message}`);
    }
  }

  close(): Promise<void> {
    return new Promise((resolve) => {
      this.consumer.disconnect((err) => {
        if (err) {
          console.error(`[consumer] disconnect error: ${err.message}`);
        }
        resolve();
      });
    });
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
