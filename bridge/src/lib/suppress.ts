/**
 * Optional pre-Kafka duplicate suppression (Phase 7, 7.3a).
 *
 * Delivery is at-least-once end to end; the real exactly-once guarantee is
 * consumer-side dedupe on the CloudEvents `id` (Phase 8). This cache is an
 * OPTIONAL bridge-side optimization (default OFF) that drops a redelivered
 * envelope within a TTL window: bounded memory (oldest evicted at max) so
 * the stateless gateway can never OOM from it. Payloads without a usable
 * `id` are never suppressed — forwarding is the safe default.
 */
export interface Suppressor {
  /** True = drop this message (a duplicate within the window). */
  shouldSuppress(payload: Buffer): boolean;
  readonly suppressedCount: number;
}

export interface SuppressorOptions {
  enabled: boolean;
  max: number;
  ttlMs: number;
  /** Injectable clock (tests). */
  now?: () => number;
}

export function createSuppressor(options: SuppressorOptions): Suppressor {
  const { enabled, max, ttlMs } = options;
  const now = options.now ?? (() => Date.now());
  const seen = new Map<string, number>();
  let suppressedCount = 0;

  return {
    get suppressedCount(): number {
      return suppressedCount;
    },
    shouldSuppress(payload: Buffer): boolean {
      if (!enabled) {
        return false;
      }
      let id: string | undefined;
      try {
        const parsed: unknown = JSON.parse(payload.toString("utf8"));
        if (parsed !== null && typeof parsed === "object" && "id" in parsed) {
          const value = (parsed as Record<string, unknown>)["id"];
          if (typeof value === "string" && value.length > 0) {
            id = value;
          }
        }
      } catch {
        return false; // not JSON → forward, never drop what we can't identify
      }
      if (id === undefined) {
        return false;
      }
      const timestamp = now();
      const last = seen.get(id);
      if (last !== undefined && timestamp - last < ttlMs) {
        suppressedCount += 1;
        seen.delete(id);
        seen.set(id, timestamp); // refresh recency
        return true;
      }
      seen.set(id, timestamp);
      if (seen.size > max) {
        const oldest = seen.keys().next().value;
        if (oldest !== undefined) {
          seen.delete(oldest);
        }
      }
      return false;
    },
  };
}
