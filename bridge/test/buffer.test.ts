import { describe, expect, it } from "vitest";
import { createBoundedBuffer } from "../src/lib/buffer.js";

describe("createBoundedBuffer", () => {
  it("is FIFO", () => {
    const buffer = createBoundedBuffer<string>(3);
    buffer.push("a");
    buffer.push("b");
    buffer.push("c");
    expect(buffer.pop()).toBe("a");
    expect(buffer.pop()).toBe("b");
    expect(buffer.pop()).toBe("c");
    expect(buffer.pop()).toBeUndefined();
  });

  it("peek does not remove", () => {
    const buffer = createBoundedBuffer<string>(2);
    buffer.push("a");
    expect(buffer.peek()).toBe("a");
    expect(buffer.size).toBe(1);
  });

  it("drops the OLDEST at the bound and counts it", () => {
    const buffer = createBoundedBuffer<number>(2);
    expect(buffer.push(1)).toBe(true);
    expect(buffer.push(2)).toBe(true);
    expect(buffer.push(3)).toBe(false); // 1 dropped
    expect(buffer.size).toBe(2);
    expect(buffer.droppedCount).toBe(1);
    expect(buffer.pop()).toBe(2); // newest kept
    expect(buffer.pop()).toBe(3);
  });

  it("counts every overflow", () => {
    const buffer = createBoundedBuffer<number>(1);
    for (let i = 0; i < 10; i += 1) {
      buffer.push(i);
    }
    expect(buffer.size).toBe(1);
    expect(buffer.droppedCount).toBe(9);
    expect(buffer.peek()).toBe(9);
  });

  it("rejects a non-positive bound", () => {
    expect(() => createBoundedBuffer<number>(0)).toThrow(/max/);
  });
});
