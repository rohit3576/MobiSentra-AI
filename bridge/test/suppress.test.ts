import { describe, expect, it } from "vitest";
import { createSuppressor } from "../src/lib/suppress.js";

function envelope(id: string): Buffer {
  return Buffer.from(JSON.stringify({ id, type: "org.mobisentra.event.fall_detected" }));
}

describe("createSuppressor", () => {
  it("disabled (default) never suppresses", () => {
    const suppressor = createSuppressor({ enabled: false, max: 10, ttlMs: 60_000 });
    expect(suppressor.shouldSuppress(envelope("a"))).toBe(false);
    expect(suppressor.shouldSuppress(envelope("a"))).toBe(false);
    expect(suppressor.suppressedCount).toBe(0);
  });

  it("suppresses a redelivery within the TTL only", () => {
    const suppressor = createSuppressor({ enabled: true, max: 10, ttlMs: 1_000 });
    expect(suppressor.shouldSuppress(envelope("a"))).toBe(false);
    expect(suppressor.shouldSuppress(envelope("a"))).toBe(true);
    expect(suppressor.suppressedCount).toBe(1);
    expect(suppressor.shouldSuppress(envelope("b"))).toBe(false);
  });

  it("TTL expiry re-admits the id", () => {
    let clock = 0;
    const suppressor = createSuppressor({ enabled: true, max: 10, ttlMs: 100, now: () => clock });
    expect(suppressor.shouldSuppress(envelope("a"))).toBe(false);
    clock = 99;
    expect(suppressor.shouldSuppress(envelope("a"))).toBe(true);
    clock = 200; // beyond the window from the ORIGINAL insert
    expect(suppressor.shouldSuppress(envelope("a"))).toBe(false);
  });

  it("evicts the oldest id at the bound (bounded memory)", () => {
    let clock = 0;
    const suppressor = createSuppressor({ enabled: true, max: 2, ttlMs: 10_000, now: () => clock });
    suppressor.shouldSuppress(envelope("first"));
    suppressor.shouldSuppress(envelope("second"));
    suppressor.shouldSuppress(envelope("third")); // size 3 > 2 → evicts "first"
    expect(suppressor.shouldSuppress(envelope("second"))).toBe(true); // still resident
    expect(suppressor.shouldSuppress(envelope("first"))).toBe(false); // was evicted
  });

  it("never suppresses what it cannot identify", () => {
    const suppressor = createSuppressor({ enabled: true, max: 10, ttlMs: 60_000 });
    expect(suppressor.shouldSuppress(Buffer.from("not json"))).toBe(false);
    expect(suppressor.shouldSuppress(Buffer.from(JSON.stringify({ no_id: 1 })))).toBe(false);
    expect(suppressor.shouldSuppress(Buffer.from(JSON.stringify({ id: 42 })))).toBe(false);
    expect(suppressor.shouldSuppress(Buffer.from(JSON.stringify({ id: "" })))).toBe(false);
    expect(suppressor.suppressedCount).toBe(0);
  });
});
