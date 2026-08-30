/**
 * Write-path pipeline (Phase 8, Step 8.3b + amendment A1).
 *
 * Per envelope: classify by CloudEvents `type` →
 *  - safety: map (8.1a) → dedupe (8.2) → PG insert `ON CONFLICT DO
 *    NOTHING` (8.3a's never-blinking net) → Redis live state → WS room
 *    emit (8.3c). Occupancy-kind safety events additionally write camera
 *    occupancy state + a `state` push (A1 live path — the edge sends
 *    to_band/count/ratio diagnostics on band flips).
 *  - analytics (`org.mobisentra.analytics.*`): Redis live state + `state`
 *    push ONLY — no PG row (`events.severity` is NOT NULL; analytics are
 *    snapshots, not incidents). No dedupe: last-wins state is idempotent
 *    under redelivery.
 *
 * Failure policy, deliberate and asymmetric: a PG failure THROWS (no
 * consumer commit → redelivery — losing an incident is the worst
 * outcome); a live-state failure DEGRADES (log + continue — the event is
 * already durable in PG). Invalid envelopes are an outcome, not a throw:
 * poison messages commit-and-skip instead of redelivering forever (DLQ
 * is Phase 10).
 */
import type { Redis } from "ioredis";
import type { CameraState, EventRecord } from "../lib/events.js";
import { envelopeKind, toAnalyticsState, toRecord } from "../lib/events.js";
import type { DedupeService } from "./dedupe.js";
import type { EventPusher } from "../ws/push.js";

export interface PgExecutor {
  query(sql: string, values?: unknown[]): Promise<{ rows: unknown[]; rowCount: number | null }>;
}

export interface EventStore {
  insert(record: EventRecord): Promise<{ inserted: boolean }>;
}

export const LIVE_STATE_TTL_SECONDS = 300;

export function liveStateKey(cameraId: string): string {
  return `camera:${cameraId}`;
}

export interface LiveStateStore {
  recordEvent(cameraId: string, info: { eventType: string; severity: string; occurredAt: string }): Promise<void>;
  recordOccupancy(cameraId: string, state: CameraState): Promise<void>;
}

export type PipelineOutcome =
  | { stage: "invalid"; kind: "safety" | "analytics"; errors: string[] }
  | { stage: "duplicate" | "conflict"; kind: "safety" }
  | { stage: "stored"; kind: "safety"; liveState: boolean; occupancyLiveState: boolean | null }
  | { stage: "live"; kind: "analytics"; liveState: boolean };

/** Adapter over pg — the ON CONFLICT net is authoritative for insert-or-duplicate. */
export class PgEventStore implements EventStore {
  constructor(private readonly exec: PgExecutor) {}

  async insert(record: EventRecord): Promise<{ inserted: boolean }> {
    const result = await this.exec.query(
      `INSERT INTO events (event_id, envelope, event_type, severity, camera_id, vehicle_id, occurred_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       ON CONFLICT (event_id) DO NOTHING`,
      [
        record.id,
        JSON.stringify(record.raw),
        record.eventType,
        record.severity,
        record.cameraId,
        record.vehicleId,
        record.occurredAt,
      ]
    );
    return { inserted: (result.rowCount ?? 0) > 0 };
  }
}

/** Adapter over ioredis — one hash per camera, TTL refreshed on every write. */
export class RedisLiveState implements LiveStateStore {
  constructor(
    private readonly client: Pick<Redis, "hset" | "expire">,
    private readonly ttlSeconds: number = LIVE_STATE_TTL_SECONDS
  ) {}

  async recordEvent(cameraId: string, info: { eventType: string; severity: string; occurredAt: string }): Promise<void> {
    const key = liveStateKey(cameraId);
    await this.client.hset(key, {
      last_event_type: info.eventType,
      severity: info.severity,
      ts: info.occurredAt,
    });
    await this.client.expire(key, this.ttlSeconds);
  }

  async recordOccupancy(cameraId: string, state: CameraState): Promise<void> {
    const key = liveStateKey(cameraId);
    const fields: Record<string, string> = { ts: state.ts };
    if (state.zone !== null) {
      fields.zone = state.zone;
    }
    if (state.level !== null) {
      fields.occupancy_level = state.level;
    }
    if (state.peopleCount !== null) {
      fields.people_count = String(state.peopleCount);
    }
    if (state.ratio !== null) {
      fields.occupancy_ratio = String(state.ratio);
    }
    await this.client.hset(key, fields);
    await this.client.expire(key, this.ttlSeconds);
  }
}

export class EventPipeline {
  constructor(
    private readonly store: EventStore,
    private readonly liveState: LiveStateStore,
    private readonly dedupe: DedupeService,
    private readonly pusher: EventPusher
  ) {}

  async process(envelope: unknown): Promise<PipelineOutcome> {
    return envelopeKind(envelope) === "analytics" ? this.analytics(envelope) : this.safety(envelope);
  }

  private async safety(envelope: unknown): Promise<PipelineOutcome> {
    const mapping = toRecord(envelope);
    if (!mapping.ok) {
      return { stage: "invalid", kind: "safety", errors: mapping.errors };
    }
    const record = mapping.record;
    if (!(await this.dedupe.isFirstSeen(record.source, record.id))) {
      return { stage: "duplicate", kind: "safety" };
    }
    const { inserted } = await this.store.insert(record); // throws → no commit → redelivery
    if (!inserted) {
      return { stage: "conflict", kind: "safety" }; // late wire duplicate — single row, no re-emit
    }
    const liveState = await this.degrade(`live state for ${record.cameraId}`, () =>
      this.liveState.recordEvent(record.cameraId, {
        eventType: record.eventType,
        severity: record.severity,
        occurredAt: record.occurredAt,
      })
    );
    let occupancyLiveState: boolean | null = null;
    if (record.occupancy !== null) {
      const state = cameraStateFromRecord(record);
      occupancyLiveState = await this.degrade(`occupancy state for ${record.cameraId}`, () =>
        this.liveState.recordOccupancy(record.cameraId, state)
      );
      this.pusher.publishState(record.vehicleId, state);
    }
    this.pusher.publish(record.vehicleId, record);
    return { stage: "stored", kind: "safety", liveState, occupancyLiveState };
  }

  private async analytics(envelope: unknown): Promise<PipelineOutcome> {
    const mapping = toAnalyticsState(envelope);
    if (!mapping.ok) {
      return { stage: "invalid", kind: "analytics", errors: mapping.errors };
    }
    const { state } = mapping;
    const cameraState: CameraState = {
      cameraId: state.cameraId,
      zone: state.zone,
      level: state.level,
      peopleCount: state.peopleCount,
      ratio: state.occupancyRatio,
      ts: state.occurredAt,
    };
    const liveState = await this.degrade(`analytics state for ${state.cameraId}`, () =>
      this.liveState.recordOccupancy(state.cameraId, cameraState)
    );
    this.pusher.publishState(state.vehicleId, cameraState);
    return { stage: "live", kind: "analytics", liveState };
  }

  /** Live-state failures never lose an event that PG already holds. */
  private async degrade(what: string, op: () => Promise<void>): Promise<boolean> {
    try {
      await op();
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error(`[pipeline] ${what} failed (${message}) — degrading (event stays durable in PG)`);
      return false;
    }
  }
}

function cameraStateFromRecord(record: EventRecord): CameraState {
  const occupancy = record.occupancy;
  if (occupancy === null) {
    throw new Error("cameraStateFromRecord on a record without occupancy info");
  }
  return {
    cameraId: record.cameraId,
    zone: occupancy.zone,
    level: occupancy.level,
    peopleCount: occupancy.peopleCount,
    ratio: occupancy.ratio,
    ts: record.occurredAt,
  };
}
