/**
 * Bounded FIFO buffer (Phase 7, 7.3b).
 *
 * The bridge's backpressure middle ground: while Kafka is down, MQTT
 * consumption pauses — but messages already in flight must not OOM the
 * gateway. Overflow drops the OLDEST buffered item and counts it (drops
 * are never silent); the edge spool still holds the source of truth for
 * full replay, so a counted drop here is degraded-but-safe.
 */
export interface BoundedBuffer<T> {
  /** False = the buffer was full; the oldest item was dropped and counted. */
  push(item: T): boolean;
  /** Oldest item without removing it. */
  peek(): T | undefined;
  /** Remove and return the oldest item. */
  pop(): T | undefined;
  readonly size: number;
  readonly droppedCount: number;
}

export function createBoundedBuffer<T>(max: number): BoundedBuffer<T> {
  if (max < 1) {
    throw new Error(`BoundedBuffer max must be ≥ 1, got ${max}`);
  }
  const items: T[] = [];
  let droppedCount = 0;
  return {
    get size(): number {
      return items.length;
    },
    get droppedCount(): number {
      return droppedCount;
    },
    push(item: T): boolean {
      items.push(item);
      if (items.length > max) {
        items.shift(); // drop the oldest — newest wins (freshest state matters most)
        droppedCount += 1;
        return false;
      }
      return true;
    },
    peek(): T | undefined {
      return items[0];
    },
    pop(): T | undefined {
      return items.shift();
    },
  };
}
