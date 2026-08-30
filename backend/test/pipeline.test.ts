import { describe, expect, it, vi } from "vitest";
import {
  EventPipeline,
  LIVE_STATE_TTL_SECONDS,
  liveStateKey,
  PgEventStore,
  RedisLiveState,
} from "../src/consumer/pipeline.js";
import type { EventStore, LiveStateStore, PgExecutor } from "../src/consumer/pipeline.js";
import { DedupeService } from "../src/consumer/dedupe.js";
import type { DedupeStore } from "../src/consumer/dedupe.js";
import type { EventPusher } from "../src/ws/push.js";
import type { CameraState, EventRecord } from "../src/lib/events.js";
import { toRecord } from "../src/lib/events.js";
import { loadExample } from "../src/schema/events.js";

class FakeDedupeStore implements DedupeStore {
  private readonly seen = new Set<string>();
  down = false;

  async firstSeen(key: string, _ttlSeconds: number): Promise<boolean> {
    if (this.down) {
      throw new Error("redis unreachable");
    }
    if (this.seen.has(key)) {
      return false;
    }
    this.seen.add(key);
    return true;
  }
}

class FakeEventStore implements EventStore {
  readonly inserts: EventRecord[] = [];
  result: { inserted: boolean } = { inserted: true };
  down = false;

  async insert(record: EventRecord): Promise<{ inserted: boolean }> {
    if (this.down) {
      throw new Error("pg down");
    }
    this.inserts.push(record);
    return this.result;
  }
}

class FakeLiveState implements LiveStateStore {
  readonly events: Array<{ cameraId: string; info: { eventType: string; severity: string; occurredAt: string } }> = [];
  readonly occupancies: Array<{ cameraId: string; state: CameraState }> = [];
  down = false;

  async recordEvent(cameraId: string, info: { eventType: string; severity: string; occurredAt: string }): Promise<void> {
    if (this.down) {
      throw new Error("redis down");
    }
    this.events.push({ cameraId, info });
  }

  async recordOccupancy(cameraId: string, state: CameraState): Promise<void> {
    if (this.down) {
      throw new Error("redis down");
    }
    this.occupancies.push({ cameraId, state });
  }
}

class RecordingPusher implements EventPusher {
  readonly events: Array<{ vehicleId: string; event: EventRecord }> = [];
  readonly states: Array<{ vehicleId: string; state: CameraState }> = [];

  publish(vehicleId: string, event: EventRecord): void {
    this.events.push({ vehicleId, event });
  }

  publishState(vehicleId: string, state: CameraState): void {
    this.states.push({ vehicleId, state });
  }
}

function setup() {
  const store = new FakeEventStore();
  const liveState = new FakeLiveState();
  const dedupeStore = new FakeDedupeStore();
  const pusher = new RecordingPusher();
  const pipeline = new EventPipeline(store, liveState, new DedupeService(dedupeStore), pusher);
  return { pipeline, store, liveState, dedupeStore, pusher };
}

function occupancyEnvelope(): Record<string, unknown> {
  return {
    specversion: "1.0",
    id: "10000000-0000-4000-8000-000000000001",
    source: "/mobisentra/edge/BUS_102/CAM_04",
    type: "org.mobisentra.event.occupancy_level_change",
    time: "2026-08-30T09:00:00Z",
    datacontenttype: "application/json",
    data: {
      event_type: "occupancy_level_change",
      severity: "LOW",
      camera_id: "BUS_102_CAM_04",
      timestamp: "2026-08-30T09:00:00Z",
      confidence: 1.0,
      tracks: [],
      zone: "cabin",
      from_band: "NORMAL",
      to_band: "MODERATE",
      count: 14,
      ratio: 0.74,
    },
  };
}

function analyticsEnvelope(): Record<string, unknown> {
  return {
    specversion: "1.0",
    id: "20000000-0000-4000-8000-000000000002",
    source: "/mobisentra/edge/BUS_102/CAM_04",
    type: "org.mobisentra.analytics.occupancy.v0",
    time: "2026-08-30T09:00:05Z",
    datacontenttype: "application/json",
    data: {
      camera_id: "BUS_102_CAM_04",
      zone: "cabin",
      people_count: 14,
      occupancy_ratio: 0.74,
      level: "MODERATE",
      ts: "2026-08-30T09:00:05Z",
    },
  };
}

describe("EventPipeline — safety branch", () => {
  it("fresh event lands in all three stores", async () => {
    const { pipeline, store, liveState, pusher } = setup();
    const outcome = await pipeline.process(loadExample("fall_envelope"));

    expect(outcome).toEqual({ stage: "stored", kind: "safety", liveState: true, occupancyLiveState: null });
    expect(store.inserts).toHaveLength(1);
    expect(store.inserts[0]?.id).toBe("0d7ee6e1-7f42-4a6b-9c3d-2b1a5f8e9d01");
    expect(liveState.events).toEqual([
      {
        cameraId: "BUS_102_CAM_04",
        info: { eventType: "fall_detected", severity: "HIGH", occurredAt: "2026-08-25T13:20:42Z" },
      },
    ]);
    expect(pusher.events).toHaveLength(1);
    expect(pusher.events[0]?.vehicleId).toBe("BUS_102");
    expect(pusher.states).toHaveLength(0); // fall carries no occupancy diagnostics
  });

  it("occupancy-kind event also writes camera state + state push (A1 live path)", async () => {
    const { pipeline, store, liveState, pusher } = setup();
    const outcome = await pipeline.process(occupancyEnvelope());

    expect(outcome).toEqual({ stage: "stored", kind: "safety", liveState: true, occupancyLiveState: true });
    expect(store.inserts).toHaveLength(1);
    expect(liveState.occupancies).toEqual([
      {
        cameraId: "BUS_102_CAM_04",
        state: { cameraId: "BUS_102_CAM_04", zone: "cabin", level: "MODERATE", peopleCount: 14, ratio: 0.74, ts: "2026-08-30T09:00:00Z" },
      },
    ]);
    expect(pusher.states).toEqual([
      {
        vehicleId: "BUS_102",
        state: { cameraId: "BUS_102_CAM_04", zone: "cabin", level: "MODERATE", peopleCount: 14, ratio: 0.74, ts: "2026-08-30T09:00:00Z" },
      },
    ]);
    expect(pusher.events).toHaveLength(1); // the incident itself still pushes
  });

  it("redis-dedupe duplicate → nothing downstream", async () => {
    const { pipeline, store, liveState, pusher } = setup();
    await pipeline.process(loadExample("fall_envelope"));
    const second = await pipeline.process(loadExample("fall_envelope"));

    expect(second).toEqual({ stage: "duplicate", kind: "safety" });
    expect(store.inserts).toHaveLength(1);
    expect(liveState.events).toHaveLength(1);
    expect(pusher.events).toHaveLength(1);
  });

  it("PG conflict (late wire duplicate past the dedupe TTL) → single row, no re-emit", async () => {
    const { pipeline, store, liveState, pusher } = setup();
    store.result = { inserted: false };
    const outcome = await pipeline.process(loadExample("fall_envelope"));

    expect(outcome).toEqual({ stage: "conflict", kind: "safety" });
    expect(liveState.events).toHaveLength(0);
    expect(pusher.events).toHaveLength(0);
  });

  it("PG down → error propagates (no commit → redelivery)", async () => {
    const { pipeline, store, liveState, pusher } = setup();
    store.down = true;
    await expect(pipeline.process(loadExample("fall_envelope"))).rejects.toThrow(/pg down/);
    expect(liveState.events).toHaveLength(0);
    expect(pusher.events).toHaveLength(0);
  });

  it("Redis live-state failure → event still stored + pushed (degrade, logged)", async () => {
    const { pipeline, store, liveState, pusher } = setup();
    liveState.down = true;
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const outcome = await pipeline.process(loadExample("fall_envelope"));

    expect(outcome).toEqual({ stage: "stored", kind: "safety", liveState: false, occupancyLiveState: null });
    expect(store.inserts).toHaveLength(1);
    expect(pusher.events).toHaveLength(1);
    expect(errorSpy).toHaveBeenCalledOnce();
    errorSpy.mockRestore();
  });

  it("invalid envelope → skipped as an outcome (poison commits, never redelivers)", async () => {
    const { pipeline, store, liveState, pusher } = setup();
    const broken = loadExample("fall_envelope") as Record<string, unknown>;
    const data = broken["data"] as Record<string, unknown>;
    data["severity"] = "EXTREME";
    const outcome = await pipeline.process(broken);

    expect(outcome.stage).toBe("invalid");
    if (outcome.stage === "invalid") {
      expect(outcome.kind).toBe("safety");
      expect(outcome.errors.length).toBeGreaterThan(0);
    }
    expect(store.inserts).toHaveLength(0);
    expect(liveState.events).toHaveLength(0);
    expect(pusher.events).toHaveLength(0);
  });
});

describe("EventPipeline — analytics branch (A1)", () => {
  it("analytics envelope → Redis + cameras-room state push, ZERO PG writes", async () => {
    const { pipeline, store, liveState, pusher } = setup();
    const outcome = await pipeline.process(analyticsEnvelope());

    expect(outcome).toEqual({ stage: "live", kind: "analytics", liveState: true });
    expect(store.inserts).toHaveLength(0);
    expect(liveState.occupancies).toEqual([
      {
        cameraId: "BUS_102_CAM_04",
        state: { cameraId: "BUS_102_CAM_04", zone: "cabin", level: "MODERATE", peopleCount: 14, ratio: 0.74, ts: "2026-08-30T09:00:05Z" },
      },
    ]);
    expect(pusher.states).toEqual([
      {
        vehicleId: "BUS_102",
        state: { cameraId: "BUS_102_CAM_04", zone: "cabin", level: "MODERATE", peopleCount: 14, ratio: 0.74, ts: "2026-08-30T09:00:05Z" },
      },
    ]);
    expect(pusher.events).toHaveLength(0);
  });

  it("analytics is last-wins idempotent — redelivery re-processes without dedupe", async () => {
    const { pipeline, liveState } = setup();
    await pipeline.process(analyticsEnvelope());
    const second = await pipeline.process(analyticsEnvelope());
    expect(second).toEqual({ stage: "live", kind: "analytics", liveState: true });
    expect(liveState.occupancies).toHaveLength(2);
  });

  it("invalid analytics envelope → invalid outcome, nothing touched", async () => {
    const { pipeline, store, liveState, pusher } = setup();
    const broken = analyticsEnvelope();
    delete (broken["data"] as Record<string, unknown>)["zone"];
    const outcome = await pipeline.process(broken);

    expect(outcome.stage).toBe("invalid");
    if (outcome.stage === "invalid") {
      expect(outcome.kind).toBe("analytics");
    }
    expect(store.inserts).toHaveLength(0);
    expect(liveState.occupancies).toHaveLength(0);
    expect(pusher.states).toHaveLength(0);
  });
});

describe("adapters", () => {
  it("PgEventStore: parameterized INSERT … ON CONFLICT (event_id) DO NOTHING; rowCount maps inserted", async () => {
    const calls: Array<{ sql: string; values: unknown[] }> = [];
    let rowCount = 1;
    const exec: PgExecutor = {
      query: async (sql, values) => {
        calls.push({ sql, values: values ?? [] });
        return { rows: [], rowCount };
      },
    };
    const mapping = toRecord(loadExample("fall_envelope"));
    if (!mapping.ok) {
      throw new Error("fall example must map");
    }
    const store = new PgEventStore(exec);

    await expect(store.insert(mapping.record)).resolves.toEqual({ inserted: true });
    rowCount = 0;
    await expect(store.insert(mapping.record)).resolves.toEqual({ inserted: false });

    const call = calls[0];
    if (call === undefined) {
      throw new Error("insert must issue a query");
    }
    expect(call.sql).toContain("ON CONFLICT (event_id) DO NOTHING");
    expect(call.values).toEqual([
      "0d7ee6e1-7f42-4a6b-9c3d-2b1a5f8e9d01",
      JSON.stringify(loadExample("fall_envelope")),
      "fall_detected",
      "HIGH",
      "BUS_102_CAM_04",
      "BUS_102",
      "2026-08-25T13:20:42Z",
    ]);
  });

  it("RedisLiveState: camera:{id} hash, runbook fields, TTL refreshed on every write", async () => {
    const calls: Array<{ op: string; args: unknown[] }> = [];
    const client = {
      hset: (...args: unknown[]) => {
        calls.push({ op: "hset", args });
        return Promise.resolve(1);
      },
      expire: (...args: unknown[]) => {
        calls.push({ op: "expire", args });
        return Promise.resolve(1);
      },
    };
    const live = new RedisLiveState(client);

    await live.recordEvent("BUS_102_CAM_04", { eventType: "fall_detected", severity: "HIGH", occurredAt: "2026-08-25T13:20:42Z" });
    expect(calls[0]).toEqual({
      op: "hset",
      args: [
        liveStateKey("BUS_102_CAM_04"),
        { last_event_type: "fall_detected", severity: "HIGH", ts: "2026-08-25T13:20:42Z" },
      ],
    });
    expect(calls[1]).toEqual({ op: "expire", args: [liveStateKey("BUS_102_CAM_04"), LIVE_STATE_TTL_SECONDS] });
    expect(liveStateKey("BUS_102_CAM_04")).toBe("camera:BUS_102_CAM_04");
    expect(LIVE_STATE_TTL_SECONDS).toBe(300);

    calls.length = 0;
    await live.recordOccupancy("BUS_102_CAM_04", {
      cameraId: "BUS_102_CAM_04",
      zone: null,
      level: null,
      peopleCount: null,
      ratio: null,
      ts: "2026-08-30T09:00:00Z",
    });
    expect(calls[0]?.args[1]).toEqual({ ts: "2026-08-30T09:00:00Z" }); // nulls never become "null" strings
  });
});
