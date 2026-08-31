/**
 * 9.1a's load-bearing proof: the WS client re-subscribes its room set
 * after a reconnect — against a real in-process socket.io server, not a
 * mock. Simulates the production failure mode: server-side disconnect
 * (transport drop), client auto-reconnects, rooms must come back with a
 * fresh server ack.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createServer, type Server as HttpServer } from "node:http";
import { Server, type Socket as ServerSocket } from "socket.io";
import { createDashboardWs, type ConnectionState, type DashboardWsClient } from "./client";
import type { CameraState, EventRecord } from "../domain";

interface JoinRecord {
  socketId: string;
  vehicleId: string;
  rooms: string[];
}

describe("dashboard ws client", () => {
  let httpServer: HttpServer;
  let io: Server;
  let url: string;
  let joins: JoinRecord[];
  let serverSockets: Map<string, ServerSocket>;

  beforeEach(async () => {
    httpServer = createServer();
    io = new Server(httpServer);
    await new Promise<void>((resolve) => {
      httpServer.listen(0, "127.0.0.1", resolve);
    });
    const address = httpServer.address();
    if (address === null || typeof address === "string") {
      throw new Error("expected tcp address");
    }
    url = `http://127.0.0.1:${address.port}`;

    joins = [];
    serverSockets = new Map();
    io.on("connection", (socket) => {
      serverSockets.set(socket.id, socket);
      socket.on("subscribe", (vehicleId: unknown, ack?: (joined: boolean) => void) => {
        // Mirrors the backend handler: join both rooms, then ack.
        if (typeof vehicleId === "string" && vehicleId.length > 0) {
          void socket.join(`alerts:${vehicleId}`);
          void socket.join(`cameras:${vehicleId}`);
          joins.push({ socketId: socket.id, vehicleId, rooms: [...socket.rooms] });
          ack?.(true);
        } else {
          ack?.(false);
        }
      });
    });
  });

  afterEach(async () => {
    io.close();
    await new Promise<void>((resolve) => {
      httpServer.close(() => resolve());
    });
  });

  function createClient(): DashboardWsClient {
    return createDashboardWs({
      url,
      reconnectionDelay: 20,
      reconnectionDelayMax: 50,
      ackTimeoutMs: 2_000,
    });
  }

  async function waitForJoins(vehicleId: string, count: number): Promise<JoinRecord[]> {
    const matching = () => joins.filter((join) => join.vehicleId === vehicleId);
    const deadline = Date.now() + 5_000;
    while (matching().length < count) {
      if (Date.now() > deadline) {
        throw new Error(
          `timeout waiting for ${count} joins of ${vehicleId}; got ${JSON.stringify(joins)}`
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    return matching();
  }

  /** The real proof: a NEW connection (new sid) re-joins after the drop. */
  async function waitForJoinFromNewSid(vehicleId: string, knownSids: Set<string>): Promise<JoinRecord> {
    const deadline = Date.now() + 5_000;
    for (;;) {
      const fresh = joins.find(
        (join) => join.vehicleId === vehicleId && !knownSids.has(join.socketId)
      );
      if (fresh !== undefined) {
        return fresh;
      }
      if (Date.now() > deadline) {
        throw new Error(
          `timeout waiting for re-join of ${vehicleId} from a new sid; got ${JSON.stringify(joins)}`
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
  }

  it("re-subscribes its room set after a forced disconnect (the classic dead-feed trap)", async () => {
    const client = createClient();
    const states: ConnectionState[] = [];
    client.onConnectionChange((state) => states.push(state));
    client.connect();

    const joined = await client.subscribeVehicle("BUS-01");
    expect(joined).toBe(true);

    // Initial connection may legitimately record two joins (the user's
    // subscribe + the connect-handler barrier) — snapshot every sid seen.
    await waitForJoins("BUS-01", 1);
    const knownSids = new Set(joins.map((join) => join.socketId));
    const firstJoin = joins.find((entry) => entry.vehicleId === "BUS-01");
    if (firstJoin === undefined) {
      throw new Error("unreachable: at least one join recorded above");
    }
    expect(firstJoin.rooms).toContain("alerts:BUS-01");
    expect(firstJoin.rooms).toContain("cameras:BUS-01");

    // Simulate a network drop: engine-level close → client sees
    // "transport close" and must auto-reconnect. (socket.disconnect(true)
    // would be "io server disconnect" — the client intentionally does
    // NOT reconnect from a server refusal.)
    serverSockets.get(firstJoin.socketId)?.conn.close();

    // Client reconnects on its own and MUST re-subscribe from the NEW
    // connection with a fresh ack.
    const rejoin = await waitForJoinFromNewSid("BUS-01", knownSids);
    expect(rejoin.rooms).toContain("alerts:BUS-01");
    expect(rejoin.rooms).toContain("cameras:BUS-01");

    expect(states).toContain("connected");
    expect(states).toContain("reconnecting");
    client.close();
  });

  it("resolves false when the server rejects the subscribe (empty vehicleId)", async () => {
    const client = createClient();
    client.connect();
    expect(await client.subscribeVehicle("   ")).toBe(false);
    client.close();
  });

  it("forwards event and state payloads to registered listeners", async () => {
    const client = createClient();
    client.connect();
    await client.subscribeVehicle("BUS-02");
    await waitForJoins("BUS-02", 1);

    const events: EventRecord[] = [];
    const states: CameraState[] = [];
    const offEvent = client.onEvent((record) => events.push(record));
    client.onState((state) => states.push(state));

    const record: EventRecord = {
      id: "evt-1",
      source: "/mobisentra/edge/BUS-02/cam-front",
      vehicleId: "BUS-02",
      cameraId: "cam-front",
      eventType: "person_fall",
      severity: "HIGH",
      occurredAt: new Date().toISOString(),
      tracks: [3],
      location: null,
      evidenceRef: null,
      modelVersions: {},
      occupancy: null,
      raw: {},
    };
    const state: CameraState = {
      cameraId: "cam-front",
      zone: null,
      level: "CROWDED",
      peopleCount: 42,
      ratio: 0.9,
      ts: new Date().toISOString(),
    };
    io.to("alerts:BUS-02").emit("event", record);
    io.to("cameras:BUS-02").emit("state", state);

    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(events).toEqual([record]);
    expect(states).toEqual([state]);

    offEvent();
    io.to("alerts:BUS-02").emit("event", record);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(events).toHaveLength(1);
    client.close();
  });
});
