import { describe, expect, it, vi } from "vitest";
import { DedupeService, DEDUPE_TTL_SECONDS, RedisDedupe, dedupeKey } from "../src/consumer/dedupe.js";

class FakeStore {
  setCalls: Array<{ key: string; ttlSeconds: number }> = [];
  private readonly seen = new Set<string>();
  down = false;

  firstSeen(key: string, ttlSeconds: number): Promise<boolean> {
    this.setCalls.push({ key, ttlSeconds });
    if (this.down) {
      return Promise.reject(new Error("redis unreachable"));
    }
    if (this.seen.has(key)) {
      return Promise.resolve(false);
    }
    this.seen.add(key);
    return Promise.resolve(true);
  }
}

describe("dedupeKey", () => {
  it("is the runbook key shape", () => {
    expect(dedupeKey("/mobisentra/edge/BUS_1/CAM_1", "abc")).toBe(
      "dedupe:/mobisentra/edge/BUS_1/CAM_1:abc"
    );
  });
});

describe("DedupeService", () => {
  it("first sighting processes, repeat skips, TTL is the runbook default", async () => {
    const store = new FakeStore();
    const service = new DedupeService(store);
    await expect(service.isFirstSeen("src", "e1")).resolves.toBe(true);
    await expect(service.isFirstSeen("src", "e1")).resolves.toBe(false);
    await expect(service.isFirstSeen("src", "e2")).resolves.toBe(true); // per-id granularity
    expect(store.setCalls[0]).toEqual({ key: "dedupe:src:e1", ttlSeconds: DEDUPE_TTL_SECONDS });
    expect(store.setCalls[0]?.ttlSeconds).toBe(86_400);
  });

  it("honors a custom TTL", async () => {
    const store = new FakeStore();
    await new DedupeService(store, 60).isFirstSeen("s", "e");
    expect(store.setCalls[0]?.ttlSeconds).toBe(60);
  });

  it("fails OPEN on store failure (process anyway — PG still guards)", async () => {
    const store = new FakeStore();
    store.down = true;
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const service = new DedupeService(store);
    await expect(service.isFirstSeen("s", "e")).resolves.toBe(true);
    expect(errorSpy).toHaveBeenCalledOnce();
    errorSpy.mockRestore();
  });
});

describe("RedisDedupe adapter", () => {
  it("maps SET NX result OK → first, null → duplicate, passing EX ttl + NX", async () => {
    const calls: unknown[][] = [];
    const okClient = {
      set: (...args: unknown[]) => {
        calls.push(args);
        return Promise.resolve("OK");
      },
    };
    const first = new RedisDedupe(okClient);
    await expect(first.firstSeen("k", 86_400)).resolves.toBe(true);
    expect(calls[0]).toEqual(["k", "1", "EX", 86_400, "NX"]);

    const duplicateClient = { set: () => Promise.resolve(null) };
    const repeat = new RedisDedupe(duplicateClient);
    await expect(repeat.firstSeen("k", 86_400)).resolves.toBe(false);
  });
});
