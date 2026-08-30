import { describe, expect, it } from "vitest";
import {
  type ConsumedMessage,
  type ConsumerDriver,
  EventConsumer,
  type MessageProcessor,
} from "../src/consumer/kafka.js";

function message(n: number, partition = 0): ConsumedMessage {
  return {
    topic: "mobisentra.events",
    partition,
    offset: BigInt(n),
    value: Buffer.from(`{"id": "m${n}"}`),
  };
}

class FakeDriver implements ConsumerDriver {
  batches: ConsumedMessage[][] = [];
  commits: ConsumedMessage[][] = [];
  fetchCount = 0;
  closed = false;
  onFetch: (() => void) | null = null;

  fetch(_timeoutMs: number): Promise<ConsumedMessage[]> {
    this.fetchCount += 1;
    this.onFetch?.();
    // yield to the event loop — an instantly-resolving promise would let the
    // consume loop spin forever in microtasks and starve the stop-timers
    return new Promise((resolve) => {
      setTimeout(() => resolve(this.batches.shift() ?? []), 0);
    });
  }

  commit(messages: ConsumedMessage[]): Promise<void> {
    this.commits.push(messages);
    return Promise.resolve();
  }

  close(): Promise<void> {
    this.closed = true;
    return Promise.resolve();
  }
}

class RecordingProcessor implements MessageProcessor {
  processed: ConsumedMessage[] = [];
  events: string[] = [];

  process(message: ConsumedMessage): Promise<void> {
    this.processed.push(message);
    this.events.push(`process:${String(message.offset)}`);
    return Promise.resolve();
  }
}

describe("EventConsumer", () => {
  it("processes one batch fully, commits it exactly once, then stops clean", async () => {
    const driver = new FakeDriver();
    const processor = new RecordingProcessor();
    driver.batches = [[message(1), message(2), message(3)]];
    const consumer = new EventConsumer({ driver, processor });
    const running = consumer.run();
    let stopping: Promise<void> = Promise.resolve();
    setTimeout(() => {
      stopping = consumer.stop();
    }, 20);
    await running;
    await stopping;
    expect(processor.events).toEqual(["process:1", "process:2", "process:3"]);
    expect(driver.commits.length).toBe(1);
    expect(driver.commits[0]?.map((m) => String(m.offset))).toEqual(["1", "2", "3"]);
    expect(driver.closed).toBe(true);
  });

  it("commits only after the whole batch is processed (ordering proof)", async () => {
    const driver = new FakeDriver();
    const order: string[] = [];
    const processor: MessageProcessor = {
      process: (message) => {
        order.push(`process:${String(message.offset)}`);
        return Promise.resolve();
      },
    };
    driver.batches = [[message(1), message(2)]];
    driver.fetch = (timeoutMs: number) => {
      driver.fetchCount += 1;
      const batch = driver.batches.shift() ?? [];
      if (batch.length > 0) {
        order.push(`fetch:${driver.fetchCount}`);
      }
      return new Promise((resolve) => setTimeout(() => resolve(batch), 0)); // event-loop yield
    };
    const originalCommit = driver.commit.bind(driver);
    driver.commit = (messages: ConsumedMessage[]) => {
      order.push("commit");
      return originalCommit(messages);
    };
    const consumer = new EventConsumer({ driver, processor });
    const running = consumer.run();
    setTimeout(() => void consumer.stop(), 0);
    await running;
    expect(order).toEqual(["fetch:1", "process:1", "process:2", "commit"]);
  });

  it("processor failure: rejects, zero commits, no further fetches", async () => {
    const driver = new FakeDriver();
    const failure = new Error("pipeline down");
    let calls = 0;
    const processor: MessageProcessor = {
      process: (message) => {
        calls += 1;
        if (String(message.offset) === "2") {
          return Promise.reject(failure);
        }
        return Promise.resolve();
      },
    };
    driver.batches = [[message(1), message(2)], [message(3)]];
    const consumer = new EventConsumer({ driver, processor });
    await expect(consumer.run()).rejects.toBe(failure);
    expect(calls).toBe(2);
    expect(driver.commits).toEqual([]); // nothing committed past the failure
    expect(driver.fetchCount).toBe(1); // never fetched the next batch
  });

  it("commit failure rejects the loop (wiring errors stay visible)", async () => {
    const driver = new FakeDriver();
    driver.batches = [[message(1)]];
    driver.commit = () => Promise.reject(new Error("commit failed"));
    const consumer = new EventConsumer({ driver, processor: new RecordingProcessor() });
    await expect(consumer.run()).rejects.toThrow("commit failed");
  });

  it("graceful stop: in-flight batch finishes, commits, driver closed, run resolves", async () => {
    const driver = new FakeDriver();
    const processed: string[] = [];
    let release: (() => void) | null = null;
    const processor: MessageProcessor = {
      process: (message) => {
        processed.push(String(message.offset));
        if (String(message.offset) === "1") {
          return new Promise<void>((resolve) => {
            release = resolve;
          });
        }
        return Promise.resolve();
      },
    };
    driver.batches = [[message(1), message(2)]];
    const consumer = new EventConsumer({ driver, processor });
    const running = consumer.run();
    await new Promise((resolve) => setTimeout(resolve, 10)); // mid-batch, blocked on message 1
    const stopping = consumer.stop();
    release?.();
    await running;
    await stopping;
    expect(processed).toEqual(["1", "2"]); // batch finished, not abandoned
    expect(driver.commits.length).toBe(1); // and committed
    expect(driver.closed).toBe(true);
  });

  it("empty fetches neither process nor commit", async () => {
    const driver = new FakeDriver();
    const processor = new RecordingProcessor();
    driver.batches = [[], []];
    const consumer = new EventConsumer({ driver, processor });
    const running = consumer.run();
    setTimeout(() => void consumer.stop(), 20);
    await running;
    expect(processor.processed).toEqual([]);
    expect(driver.commits).toEqual([]);
  });
});
