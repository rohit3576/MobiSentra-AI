import { describe, expect, it } from "vitest";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createApiServer } from "../src/api/server.js";
import { encodeCursor } from "../src/api/store.js";
import type {
  ApiStore,
  CameraRow,
  CameraStatusStore,
  EventHistoryPage,
  IncidentDetail,
  IncidentFilters,
  IncidentSummary,
} from "../src/api/store.js";

class FakeApiStore implements ApiStore {
  incidentsFilters: IncidentFilters[] = [];
  incidentsResult: IncidentSummary[] = [];
  detail: IncidentDetail | null = null;
  detailId: string | null = null;
  eventsPages: EventHistoryPage[] = [];
  eventsCursors: Array<{ occurredAt: string; eventId: string } | null> = [];
  eventsLimits: number[] = [];
  camerasResult: CameraRow[] = [];
  ackCalls: Array<{ id: string; actor: string }> = [];
  escalateCalls: Array<{ id: string; actor: string; detail: unknown }> = [];
  ackResult: { updated: boolean } = { updated: true };
  escalateResult: { updated: boolean } = { updated: true };

  async incidents(filters: IncidentFilters): Promise<IncidentSummary[]> {
    this.incidentsFilters.push(filters);
    return this.incidentsResult;
  }

  async incident(id: string): Promise<IncidentDetail | null> {
    this.detailId = id;
    return this.detail;
  }

  async events(
    cursor: { occurredAt: string; eventId: string } | null,
    limit: number
  ): Promise<EventHistoryPage> {
    this.eventsCursors.push(cursor);
    this.eventsLimits.push(limit);
    return this.eventsPages.shift() ?? { events: [], next: null };
  }

  async cameras(): Promise<CameraRow[]> {
    return this.camerasResult;
  }

  async acknowledge(id: string, actor: string): Promise<{ updated: boolean }> {
    this.ackCalls.push({ id, actor });
    return this.ackResult;
  }

  async escalate(id: string, actor: string, detail: unknown): Promise<{ updated: boolean }> {
    this.escalateCalls.push({ id, actor, detail });
    return this.escalateResult;
  }
}

class FakeStatus implements CameraStatusStore {
  statusMap: Record<string, boolean> = {};

  async online(cameraIds: string[]): Promise<Record<string, boolean>> {
    return Object.fromEntries(cameraIds.map((id) => [id, this.statusMap[id] === true]));
  }
}

const summary: IncidentSummary = {
  eventId: "e1",
  eventType: "fall_detected",
  severity: "HIGH",
  cameraId: "BUS_1_CAM_1",
  vehicleId: "BUS_1",
  occurredAt: "2026-08-30T10:00:00Z",
  ackedAt: null,
};

function server() {
  const store = new FakeApiStore();
  const status = new FakeStatus();
  const app = createApiServer(store, status);
  return { app, store, status };
}

describe("GET /api/incidents", () => {
  it("happy path with default filters (limit 50, everything null)", async () => {
    const { app, store } = server();
    store.incidentsResult = [summary];
    const response = await app.inject({ method: "GET", url: "/api/incidents" });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual([summary]);
    expect(store.incidentsFilters[0]).toEqual({
      severity: null,
      cameraId: null,
      vehicleId: null,
      since: null,
      until: null,
      acked: null,
      limit: 50,
    });
  });

  it("forwards every filter, parsed", async () => {
    const { app, store } = server();
    await app.inject({
      method: "GET",
      url: "/api/incidents?severity=HIGH&camera=BUS_1_CAM_1&vehicle=BUS_1&since=2026-08-30T00:00:00Z&until=2026-08-31T00:00:00Z&acked=false&limit=10",
    });
    expect(store.incidentsFilters[0]).toEqual({
      severity: "HIGH",
      cameraId: "BUS_1_CAM_1",
      vehicleId: "BUS_1",
      since: "2026-08-30T00:00:00Z",
      until: "2026-08-31T00:00:00Z",
      acked: false,
      limit: 10,
    });
  });

  it("rejects bad severity, limit, timestamp, and acked values", async () => {
    const { app } = server();
    expect((await app.inject({ method: "GET", url: "/api/incidents?severity=EXTREME" })).statusCode).toBe(400);
    expect((await app.inject({ method: "GET", url: "/api/incidents?limit=0" })).statusCode).toBe(400);
    expect((await app.inject({ method: "GET", url: "/api/incidents?limit=201" })).statusCode).toBe(400);
    expect((await app.inject({ method: "GET", url: "/api/incidents?since=yesterday" })).statusCode).toBe(400);
    expect((await app.inject({ method: "GET", url: "/api/incidents?acked=maybe" })).statusCode).toBe(400);
  });
});

describe("GET /api/incidents/:id", () => {
  it("returns the detail or 404", async () => {
    const { app, store } = server();
    store.detail = { ...summary, evidenceRef: "local://evidence/BUS_1_CAM_1/e1.mp4", escalation: null, ackedBy: null, envelope: { id: "e1" } };
    const ok = await app.inject({ method: "GET", url: "/api/incidents/e1" });
    expect(ok.statusCode).toBe(200);
    expect(ok.json().evidenceRef).toBe("local://evidence/BUS_1_CAM_1/e1.mp4");
    store.detail = null;
    const missing = await app.inject({ method: "GET", url: "/api/incidents/nope" });
    expect(missing.statusCode).toBe(404);
  });
});

describe("GET /api/cameras", () => {
  it("merges PG rows with Redis live status", async () => {
    const { app, store, status } = server();
    store.camerasResult = [
      { cameraId: "BUS_1_CAM_1", vehicleId: "BUS_1" },
      { cameraId: "BUS_2_CAM_1", vehicleId: "BUS_2" },
    ];
    status.statusMap = { BUS_1_CAM_1: true };
    const response = await app.inject({ method: "GET", url: "/api/cameras" });
    expect(response.json()).toEqual([
      { cameraId: "BUS_1_CAM_1", vehicleId: "BUS_1", online: true },
      { cameraId: "BUS_2_CAM_1", vehicleId: "BUS_2", online: false },
    ]);
  });
});

describe("GET /api/events", () => {
  it("walks pages by cursor; malformed cursor → 400", async () => {
    const { app, store } = server();
    const cursor = encodeCursor({ occurredAt: "2026-08-30T09:00:00Z", eventId: "e5" });
    store.eventsPages = [
      { events: [], next: cursor },
      { events: [], next: null },
    ];
    const first = await app.inject({ method: "GET", url: "/api/events?limit=25" });
    expect(first.statusCode).toBe(200);
    expect(first.json().next).toBe(cursor);
    const second = await app.inject({ method: "GET", url: `/api/events?cursor=${cursor}` });
    expect(second.json().next).toBeNull();
    expect(store.eventsCursors).toEqual([null, { occurredAt: "2026-08-30T09:00:00Z", eventId: "e5" }]);
    expect(store.eventsLimits).toEqual([25, 50]);
    expect((await app.inject({ method: "GET", url: "/api/events?cursor=%%%bad" })).statusCode).toBe(400);
  });
});

describe("POST /api/incidents/:id/ack + /escalate", () => {
  it("ack: actor required, forwarded, 404 when the event is absent", async () => {
    const { app, store } = server();
    const ok = await app.inject({ method: "POST", url: "/api/incidents/e1/ack", payload: { actor: "operator-1" } });
    expect(ok.statusCode).toBe(200);
    expect(ok.json()).toEqual({ ok: true });
    expect(store.ackCalls).toEqual([{ id: "e1", actor: "operator-1" }]);

    store.ackResult = { updated: false };
    expect((await app.inject({ method: "POST", url: "/api/incidents/e1/ack", payload: { actor: "x" } })).statusCode).toBe(404);
    expect((await app.inject({ method: "POST", url: "/api/incidents/e1/ack", payload: {} })).statusCode).toBe(400);
    expect((await app.inject({ method: "POST", url: "/api/incidents/e1/ack", payload: { actor: "" } })).statusCode).toBe(400);
  });

  it("escalate: actor + optional detail forwarded", async () => {
    const { app, store } = server();
    await app.inject({
      method: "POST",
      url: "/api/incidents/e1/escalate",
      payload: { actor: "operator-1", detail: { reason: "medical" } },
    });
    expect(store.escalateCalls).toEqual([{ id: "e1", actor: "operator-1", detail: { reason: "medical" } }]);
    store.escalateResult = { updated: false };
    expect((await app.inject({ method: "POST", url: "/api/incidents/e1/escalate", payload: { actor: "x" } })).statusCode).toBe(404);
  });
});

describe("GET /api/evidence/* (A2)", () => {
  it("serves an mp4 fixture, honors Range, rejects traversal, 404s missing, 503s unconfigured", async () => {
    const root = await mkdtemp(join(tmpdir(), "mobisentra-evidence-"));
    await mkdir(join(root, "BUS_1_CAM_1"));
    const bytes = Buffer.from([0, 1, 2, 3, 4, 5, 6, 7]);
    await writeFile(join(root, "BUS_1_CAM_1", "clip.mp4"), bytes);

    const store = new FakeApiStore();
    const app = createApiServer(store, new FakeStatus(), { evidenceRoot: root });

    const full = await app.inject({ method: "GET", url: "/api/evidence/BUS_1_CAM_1/clip.mp4" });
    expect(full.statusCode).toBe(200);
    expect(full.headers["content-type"]).toBe("video/mp4");
    expect(full.headers["accept-ranges"]).toBe("bytes");
    expect(Buffer.from(full.rawPayload).equals(bytes)).toBe(true);

    const partial = await app.inject({
      method: "GET",
      url: "/api/evidence/BUS_1_CAM_1/clip.mp4",
      headers: { range: "bytes=2-5" },
    });
    expect(partial.statusCode).toBe(206);
    expect(partial.headers["content-range"]).toBe("bytes 2-5/8");
    expect(Buffer.from(partial.rawPayload).equals(Buffer.from([2, 3, 4, 5]))).toBe(true);

    const suffix = await app.inject({
      method: "GET",
      url: "/api/evidence/BUS_1_CAM_1/clip.mp4",
      headers: { range: "bytes=-3" },
    });
    expect(suffix.statusCode).toBe(206);
    expect(Buffer.from(suffix.rawPayload).equals(Buffer.from([5, 6, 7]))).toBe(true);

    const beyond = await app.inject({
      method: "GET",
      url: "/api/evidence/BUS_1_CAM_1/clip.mp4",
      headers: { range: "bytes=99-" },
    });
    expect(beyond.statusCode).toBe(416);

    const escape = await app.inject({ method: "GET", url: "/api/evidence/..%2F..%2Fsecret.mp4" });
    expect([403, 404]).toContain(escape.statusCode); // 403 when decoded, 404 when not — never a leak

    const missing = await app.inject({ method: "GET", url: "/api/evidence/BUS_1_CAM_1/nope.mp4" });
    expect(missing.statusCode).toBe(404);

    const unconfigured = createApiServer(new FakeApiStore(), new FakeStatus());
    expect((await unconfigured.inject({ method: "GET", url: "/api/evidence/x.mp4" })).statusCode).toBe(503);
  });
});
