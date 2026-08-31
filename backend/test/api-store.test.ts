import { describe, expect, it } from "vitest";
import {
  decodeCursor,
  encodeCursor,
  PgApiStore,
  RedisCameraStatus,
} from "../src/api/store.js";
import type { PgExecutor } from "../src/api/store.js";

class FakePg implements PgExecutor {
  readonly calls: Array<{ sql: string; values: unknown[] }> = [];
  results: Array<{ rows: unknown[]; rowCount: number | null }> = [];
  failOn: string | null = null;

  async query(sql: string, values?: unknown[]) {
    if (this.failOn !== null && sql.includes(this.failOn)) {
      throw new Error("injected failure");
    }
    this.calls.push({ sql, values: values ?? [] });
    return this.results.shift() ?? { rows: [], rowCount: 0 };
  }

  last(): { sql: string; values: unknown[] } {
    const call = this.calls[this.calls.length - 1];
    if (call === undefined) {
      throw new Error("no query issued");
    }
    return call;
  }
}

describe("PgApiStore.incidents", () => {
  it("one parameterized query: OR-null filter scaffold, newest-first, limit", async () => {
    const pg = new FakePg();
    pg.results = [{ rows: [], rowCount: 0 }];
    await new PgApiStore(pg).incidents({
      severity: "HIGH",
      cameraId: "C1",
      vehicleId: "V1",
      since: "2026-08-30T00:00:00Z",
      until: null,
      acked: false,
      limit: 25,
    });
    const call = pg.last();
    expect(call.values).toEqual(["HIGH", "C1", "V1", "2026-08-30T00:00:00Z", null, false, 25]);
    expect(call.sql).toContain("ORDER BY occurred_at DESC, event_id DESC");
    expect(call.sql).toContain("$6::boolean IS NULL OR $6 = (acked_at IS NOT NULL)");
    for (const column of ["severity", "camera_id", "vehicle_id", "occurred_at"]) {
      expect(call.sql).toContain(column);
    }
  });

  it("maps rows; timestamps via Date or string", async () => {
    const pg = new FakePg();
    pg.results = [
      {
        rows: [
          {
            event_id: "e1",
            event_type: "fall_detected",
            severity: "HIGH",
            camera_id: "C1",
            vehicle_id: "V1",
            occurred_at: new Date("2026-08-30T10:00:00Z"),
            acked_at: null,
          },
        ],
        rowCount: 1,
      },
    ];
    const rows = await new PgApiStore(pg).incidents({
      severity: null, cameraId: null, vehicleId: null, since: null, until: null, acked: null, limit: 50,
    });
    expect(rows).toEqual([
      {
        eventId: "e1",
        eventType: "fall_detected",
        severity: "HIGH",
        cameraId: "C1",
        vehicleId: "V1",
        occurredAt: "2026-08-30T10:00:00.000Z",
        ackedAt: null,
      },
    ]);
  });
});

describe("PgApiStore.incident", () => {
  it("extracts evidence_ref from the envelope JSONB; 0 rows → null", async () => {
    const pg = new FakePg();
    pg.results = [
      {
        rows: [
          {
            event_id: "e1",
            event_type: "fall_detected",
            severity: "HIGH",
            camera_id: "C1",
            vehicle_id: "V1",
            occurred_at: "2026-08-30T10:00:00Z",
            acked_at: null,
            acked_by: null,
            escalation: null,
            envelope: { id: "e1" },
            evidence_ref: "local://evidence/C1/e1.mp4",
          },
        ],
        rowCount: 1,
      },
      { rows: [], rowCount: 0 },
    ];
    const store = new PgApiStore(pg);
    await expect(store.incident("e1")).resolves.toMatchObject({ evidenceRef: "local://evidence/C1/e1.mp4" });
    expect(pg.last().sql).toContain("envelope->'data'->>'evidence_ref'");
    await expect(store.incident("missing")).resolves.toBeNull();
  });
});

describe("PgApiStore.events", () => {
  it("keyset pagination: cursor params, limit+1 fetch, next cursor from the last returned row", async () => {
    const pg = new FakePg();
    const row = (id: string) => ({ event_id: id, event_type: "t", occurred_at: `2026-08-30T0${id.slice(-1)}:00:00Z`, envelope: { id } });
    pg.results = [
      { rows: [row("e1"), row("e2"), row("e3")], rowCount: 3 }, // 3 rows with limit 2 → has next
      { rows: [row("e1"), row("e2")], rowCount: 2 }, // exactly limit → no next
    ];
    const store = new PgApiStore(pg);

    const page1 = await store.events({ occurredAt: "2026-08-30T09:00:00Z", eventId: "cursor-anchor" }, 2);
    expect(page1.events.map((e) => e.eventId)).toEqual(["e1", "e2"]);
    expect(page1.next).toBe(encodeCursor({ occurredAt: "2026-08-30T02:00:00Z", eventId: "e2" }));

    const page2 = await store.events(null, 2);
    expect(page2.next).toBeNull();
    expect(page2.events).toHaveLength(2);

    expect(pg.calls[0]?.values).toEqual(["2026-08-30T09:00:00Z", "cursor-anchor", 3]);
    expect(pg.calls[1]?.values).toEqual([null, null, 3]);
    expect(pg.calls[0]?.sql).toContain("(occurred_at, event_id) < ($1, $2::text)");
  });
});

describe("PgApiStore.cameras", () => {
  it("distinct pairs, grouped in SQL", async () => {
    const pg = new FakePg();
    pg.results = [{ rows: [{ camera_id: "C1", vehicle_id: "V1" }], rowCount: 1 }];
    await new PgApiStore(pg).cameras();
    expect(pg.last().sql).toContain("GROUP BY camera_id, vehicle_id");
  });
});

describe("PgApiStore actions (ack/escalate)", () => {
  it("ack: UPDATE + audit row in ONE transaction; 0-row update → rollback, no orphan audit", async () => {
    const pg = new FakePg();
    pg.results = [
      { rows: [], rowCount: 1 }, // UPDATE hits
      { rows: [], rowCount: 1 }, // audit INSERT
    ];
    await new PgApiStore(pg).acknowledge("e1", "operator-1");
    expect(pg.calls.map((c) => c.sql)).toEqual(["BEGIN", expect.stringContaining("acked_at = now()"), expect.stringContaining("INSERT INTO audit_log"), "COMMIT"]);
    expect(pg.calls[2]?.values).toEqual(["ack", "e1", "operator-1", "{}"]);

    pg.calls.length = 0;
    pg.results = [{ rows: [], rowCount: 0 }];
    await expect(new PgApiStore(pg).acknowledge("e1", "operator-1")).resolves.toEqual({ updated: false });
    expect(pg.calls.map((c) => c.sql)).toEqual(["BEGIN", expect.stringContaining("acked_at = now()"), "ROLLBACK"]);
  });

  it("escalate: escalation jsonb + audit detail; failure → rollback + rethrow", async () => {
    const pg = new FakePg();
    pg.results = [{ rows: [], rowCount: 1 }, { rows: [], rowCount: 1 }];
    await new PgApiStore(pg).escalate("e1", "operator-1", { reason: "medical" });
    expect(pg.calls[1]?.values).toEqual(["e1", "operator-1", JSON.stringify({ reason: "medical" })]);
    expect(pg.calls[1]?.sql).toContain("jsonb_build_object");
    expect(pg.calls[2]?.values).toEqual(["escalate", "e1", "operator-1", JSON.stringify({ reason: "medical" })]);

    pg.calls.length = 0;
    pg.failOn = "jsonb_build_object"; // the escalate UPDATE
    await expect(new PgApiStore(pg).escalate("e1", "operator-1", null)).rejects.toThrow(/injected failure/);
    expect(pg.calls[pg.calls.length - 1]?.sql).toBe("ROLLBACK");
  });
});

describe("RedisCameraStatus", () => {
  it("online = ttl > 0, batched in one pipeline; empty input → no exec", async () => {
    const ttlCalls: string[] = [];
    let execCount = 0;
    const replies: Array<[Error | null, unknown]> = [
      [null, 240],
      [null, -2],
      [null, -1],
    ];
    const client = {
      pipeline: () => ({
        ttl: (key: string) => {
          ttlCalls.push(key);
          return this;
        },
        exec: async () => {
          execCount += 1;
          return replies;
        },
      }),
    };
    const status = new RedisCameraStatus(client);
    await expect(status.online(["C1", "C2", "C3"])).resolves.toEqual({ C1: true, C2: false, C3: false });
    expect(ttlCalls).toEqual(["camera:C1", "camera:C2", "camera:C3"]);
    await expect(status.online([])).resolves.toEqual({});
    expect(execCount).toBe(1); // the empty call never reached redis
  });
});

describe("cursor codec", () => {
  it("round-trips and rejects garbage", () => {
    const cursor = encodeCursor({ occurredAt: "2026-08-30T09:00:00Z", eventId: "e5" });
    expect(decodeCursor(cursor)).toEqual({ occurredAt: "2026-08-30T09:00:00Z", eventId: "e5" });
    expect(decodeCursor("%%%not-base64")).toBeNull();
    expect(decodeCursor(Buffer.from("not json").toString("base64url"))).toBeNull();
    expect(decodeCursor(Buffer.from(JSON.stringify({ o: 1, i: 2 })).toString("base64url"))).toBeNull();
  });
});
