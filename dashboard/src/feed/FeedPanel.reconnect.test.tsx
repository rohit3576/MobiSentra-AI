/**
 * 9.1b's load-bearing proof: a mid-stream transport drop loses NO
 * already-rendered feed rows, and live events keep flowing after the
 * client auto-reconnects — against a REAL in-process socket.io server
 * with the REAL ws client (the re-subscribe barrier is exercised end to
 * end, not mocked). This is the unit-level twin of the browser
 * reconnect pass in the step report.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createServer, type Server as HttpServer } from "node:http";
import { Server, type Socket as ServerSocket } from "socket.io";
import FeedPanel from "./FeedPanel";
import type { EventRecord, IncidentSummary } from "../domain";
import { createDashboardWs, type DashboardWsClient } from "../ws/client";

const VEHICLE = "BUS-9";

function incident(id: string): IncidentSummary {
  return {
    eventId: id,
    eventType: "fall_detected",
    severity: "HIGH",
    cameraId: "cam-front",
    vehicleId: VEHICLE,
    occurredAt: "2026-08-31T08:00:00Z",
    ackedAt: null,
  };
}

function eventRecord(id: string): EventRecord {
  return {
    id,
    source: `/mobisentra/edge/${VEHICLE}/cam-front`,
    vehicleId: VEHICLE,
    cameraId: "cam-front",
    eventType: "fall_detected",
    severity: "CRITICAL",
    occurredAt: "2026-08-31T08:00:01Z",
    tracks: [3],
    location: null,
    evidenceRef: null,
    modelVersions: {},
    occupancy: null,
    raw: {},
  };
}

describe("FeedPanel across a mid-stream disconnect", () => {
  let httpServer: HttpServer;
  let io: Server;
  let url: string;
  let joins: Array<{ socketId: string; vehicleId: string }>;
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
        if (typeof vehicleId === "string" && vehicleId.length > 0) {
          void socket.join(`alerts:${vehicleId}`);
          void socket.join(`cameras:${vehicleId}`);
          joins.push({ socketId: socket.id, vehicleId });
          ack?.(true);
        } else {
          ack?.(false);
        }
      });
    });
  });

  afterEach(async () => {
    cleanup();
    vi.unstubAllGlobals();
    io.close();
    await new Promise<void>((resolve) => {
      httpServer.close(() => resolve());
    });
  });

  async function waitForJoinFromNewSid(
    vehicleId: string,
    knownSids: Set<string>
  ): Promise<{ socketId: string; vehicleId: string }> {
    const deadline = Date.now() + 5_000;
    for (;;) {
      const fresh = joins.find((join) => join.vehicleId === vehicleId && !knownSids.has(join.socketId));
      if (fresh !== undefined) {
        return fresh;
      }
      if (Date.now() > deadline) {
        throw new Error(`timeout waiting for re-join; joins=${JSON.stringify(joins)}`);
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
  }

  it("keeps rendered rows across the drop and resumes live after reconnect", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => [incident("hist-1")] }))
    );

    const client: DashboardWsClient = createDashboardWs({
      url,
      reconnectionDelay: 20,
      reconnectionDelayMax: 50,
      ackTimeoutMs: 2_000,
    });
    client.connect();
    expect(await client.subscribeVehicle(VEHICLE)).toBe(true);
    await waitFor(() => expect(joins.length).toBeGreaterThan(0));
    const knownSids = new Set(joins.map((join) => join.socketId));

    const screen = render(<FeedPanel vehicleId={VEHICLE} ws={client} />);
    expect(await screen.findByTestId("feed-row")).toHaveAttribute("data-event-id", "hist-1");

    // live event before the drop
    io.to(`alerts:${VEHICLE}`).emit("event", eventRecord("live-1"));
    await waitFor(() => expect(screen.getAllByTestId("feed-row")).toHaveLength(2));

    // mid-stream transport drop (engine-level close → "transport close")
    const firstSid = joins[0]?.socketId;
    if (firstSid === undefined) {
      throw new Error("unreachable: join recorded above");
    }
    serverSockets.get(firstSid)?.conn.close();

    // client reconnects; the barrier re-subscribes from a NEW sid
    const rejoin = await waitForJoinFromNewSid(VEHICLE, knownSids);
    expect(rejoin.vehicleId).toBe(VEHICLE);

    // already-rendered rows survive the reconnect untouched
    const rowIds = screen.getAllByTestId("feed-row").map((row) => row.getAttribute("data-event-id"));
    expect(rowIds).toEqual(["live-1", "hist-1"]);

    // and live flow resumes on the new connection
    io.to(`alerts:${VEHICLE}`).emit("event", eventRecord("live-2"));
    await waitFor(() => expect(screen.getAllByTestId("feed-row")).toHaveLength(3));
    const afterIds = screen.getAllByTestId("feed-row").map((row) => row.getAttribute("data-event-id"));
    expect(afterIds).toEqual(["live-2", "live-1", "hist-1"]);

    client.close();
  });
});
