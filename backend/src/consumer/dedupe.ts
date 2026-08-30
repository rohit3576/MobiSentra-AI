/**
 * Redis dedupe (Phase 8, Step 8.2) — the at-least-once → exactly-once net.
 *
 * Kafka redelivers (the Phase-7 soak measured 6 wire duplicates around a
 * broker restart); `SET dedupe:{source}:{id} NX EX <ttl>` marks an event
 * as seen atomically — first caller processes, repeats skip. The runbook
 * TTL is 24 h, far beyond any replay window.
 *
 * Failure policy (documented decision): a dedupe outage FAILS OPEN —
 * `isFirstSeen` returns true (process) and logs. Dropping safety events
 * because Redis blinked would be the wrong trade; PG `ON CONFLICT DO
 * NOTHING` (8.3a) is the second net and never blinks with the DB down.
 */
import type { Redis } from "ioredis";

export const DEDUPE_TTL_SECONDS = 86_400;

export interface DedupeStore {
  /** True = first sighting (process); false = duplicate (skip). */
  firstSeen(key: string, ttlSeconds: number): Promise<boolean>;
}

export function dedupeKey(source: string, id: string): string {
  return `dedupe:${source}:${id}`;
}

/** Adapter over the real client's atomic SET-NX-EX. */
export class RedisDedupe implements DedupeStore {
  constructor(private readonly client: Pick<Redis, "set">) {}

  async firstSeen(key: string, ttlSeconds: number): Promise<boolean> {
    const result = await this.client.set(key, "1", "EX", ttlSeconds, "NX");
    return result === "OK";
  }
}

export class DedupeService {
  constructor(
    private readonly store: DedupeStore,
    private readonly ttlSeconds: number = DEDUPE_TTL_SECONDS
  ) {}

  async isFirstSeen(source: string, id: string): Promise<boolean> {
    try {
      return await this.store.firstSeen(dedupeKey(source, id), this.ttlSeconds);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error(`[dedupe] store failure (${message}) — failing OPEN (process; PG guards)`);
      return true;
    }
  }
}
