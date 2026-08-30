import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import type { Server as HttpServer } from "node:http";
import { io } from "socket.io-client";
import type { Socket } from "socket.io-client";
import { createPushServer, vehicleRoom } from "../src/ws/push.js";
import type { EventPusher, PushHandle } from "../src/ws/push.js";
import type { CameraState, EventRecord } from "../src/lib/events.js";

/**
 * Real socket.io server + client over an ephemeral localhost port —
 * no transport mocks. Cross-room isolation is asserted WITHOUT timing:
 * the server is single-threaded, so publish(A) is fully enqueued before
 * publish(B); if A leaked into the wrong room it would arrive first and
 * fail the strict-equal on the client's FIRST received event.
 */

let consoleSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  consoleSpy = vi.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => {
  consoleSpy.mockRestore();
});

const cleanup: Array<() => Promise<void> | void> = [];

afterEach(async () => {
  for (const teardown of cleanup.splice(0)) {
    await teardown();
  }
});

function record(id: string, vehicleId: string): EventRecord {
  return {
    id,
    source: `/mobisentra/edge/${vehicleId}/CAM_01`,
    vehicleId,
    cameraId: `${vehicleId}_CAM_01`,
    eventType: "fall_detected",
    severity: "HIGH",
    occurredAt: "2026-08-30T10:00:00Z",
    tracks: [3, 7],
    location: null,
    evidenceRef: `s3://evidence/${id}.mp4`,
    modelVersions: { detect: "yolo26n", fall: "v2" },
    raw: {
      id,
      source: `/mobisentra/edge/${vehicleId}/CAM_01`,
      specversion: "1.0",
      type: "mobisentra.event.v0",
      time: "2026-08-30T10:00:00Z",
      data: { event_type: "fall_detected", severity: "HIGH", camera_id: `${vehicleId}_CAM_01`, timestamp: "2026-08-30T10:00:00Z" },
    },
  };
}

async function startServer(): Promise<{ pusher: EventPusher; url: string }> {
  const server: HttpServer = createServer();
  const handle: PushHandle = createPushServer(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("expected a TCP port from listen(0)");
  }
  cleanup.push(async () => {
    await handle.close();
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  return { pusher: handle.pusher, url: `http://127.0.0.1:${address.port}` };
}

async function connectClient(url: string): Promise<Socket> {
  const socket = io(url, { transports: ["websocket"], reconnection: false });
  await new Promise<void>((resolve, reject) => {
    socket.once("connect", resolve);
    socket.once("connect_error", (err: Error) => reject(err));
  });
  cleanup.push(() => {
    socket.disconnect();
  });
  return socket;
}

function subscribe(socket: Socket, vehicleId: unknown): Promise<boolean> {
  return new Promise((resolve) => {
    socket.emit("subscribe", vehicleId, (joined: boolean) => resolve(joined));
  });
}

function nextEvent(socket: Socket): Promise<EventRecord> {
  return new Promise((resolve) => {
    socket.once("event", (payload: EventRecord) => resolve(payload));
  });
}

function nextState(socket: Socket): Promise<CameraState> {
  return new Promise((resolve) => {
    socket.once("state", (payload: CameraState) => resolve(payload));
  });
}

describe("vehicleRoom", () => {
  it("is the runbook room shape", () => {
    expect(vehicleRoom("BUS_102")).toBe("alerts:BUS_102");
  });
});

describe("createPushServer", () => {
  it("each client receives only its room's events, with the exact EventRecord payload", async () => {
    const { pusher, url } = await startServer();
    const busOne = await connectClient(url);
    const busTwo = await connectClient(url);
    await expect(subscribe(busOne, "BUS_1")).resolves.toBe(true);
    await expect(subscribe(busTwo, "BUS_2")).resolves.toBe(true);

    const firstOnOne = nextEvent(busOne);
    const firstOnTwo = nextEvent(busTwo);
    pusher.publish("BUS_1", record("e1", "BUS_1"));
    pusher.publish("BUS_2", record("e2", "BUS_2"));
    // a cross-room leak would have arrived FIRST and failed these strict equals
    await expect(firstOnOne).resolves.toStrictEqual(record("e1", "BUS_1"));
    await expect(firstOnTwo).resolves.toStrictEqual(record("e2", "BUS_2"));

    // second round: e3 (BUS_2) must skip busOne; busOne's next event is e4
    const secondOnOne = nextEvent(busOne);
    pusher.publish("BUS_2", record("e3", "BUS_2"));
    pusher.publish("BUS_1", record("e4", "BUS_1"));
    await expect(secondOnOne).resolves.toStrictEqual(record("e4", "BUS_1"));
  });

  it("two clients in the same room both receive the event", async () => {
    const { pusher, url } = await startServer();
    const operatorA = await connectClient(url);
    const operatorB = await connectClient(url);
    await expect(subscribe(operatorA, "BUS_9")).resolves.toBe(true);
    await expect(subscribe(operatorB, "BUS_9")).resolves.toBe(true);

    const receivedA = nextEvent(operatorA);
    const receivedB = nextEvent(operatorB);
    const shared = record("shared", "BUS_9");
    pusher.publish("BUS_9", shared);
    await expect(receivedA).resolves.toStrictEqual(shared);
    await expect(receivedB).resolves.toStrictEqual(shared);
  });

  it("invalid subscribe is rejected and does NOT join the room", async () => {
    const { pusher, url } = await startServer();
    const client = await connectClient(url);

    await expect(subscribe(client, 42)).resolves.toBe(false);
    await expect(subscribe(client, "")).resolves.toBe(false);

    // if the rejected subscribe had (buggily) joined, e1 would be this
    // client's FIRST event; ordering makes this deterministic
    pusher.publish("BUS_1", record("e1", "BUS_1"));
    await expect(subscribe(client, "BUS_2")).resolves.toBe(true);
    const first = nextEvent(client);
    pusher.publish("BUS_2", record("e2", "BUS_2"));
    await expect(first).resolves.toStrictEqual(record("e2", "BUS_2"));
  });

  it("camera state reaches only its vehicle's clients, on the state channel (A1)", async () => {
    const { pusher, url } = await startServer();
    const busOne = await connectClient(url);
    const busTwo = await connectClient(url);
    await expect(subscribe(busOne, "BUS_1")).resolves.toBe(true);
    await expect(subscribe(busTwo, "BUS_2")).resolves.toBe(true);

    const eventChannelLeaks: string[] = [];
    busOne.on("event", () => eventChannelLeaks.push("BUS_1"));
    busTwo.on("event", () => eventChannelLeaks.push("BUS_2"));

    const stateOf = (cameraId: string): CameraState => ({
      cameraId,
      zone: "cabin",
      level: "MODERATE",
      peopleCount: 14,
      ratio: 0.74,
      ts: "2026-08-30T09:00:05Z",
    });
    const stateOnOne = nextState(busOne);
    const stateOnTwo = nextState(busTwo);
    pusher.publishState("BUS_1", stateOf("BUS_1_CAM_1"));
    pusher.publishState("BUS_2", stateOf("BUS_2_CAM_1"));
    await expect(stateOnOne).resolves.toStrictEqual(stateOf("BUS_1_CAM_1"));
    await expect(stateOnTwo).resolves.toStrictEqual(stateOf("BUS_2_CAM_1"));
    expect(eventChannelLeaks).toEqual([]); // state never rides the event channel
  });

  it("logs connections (operator visibility)", async () => {
    const { url } = await startServer();
    await connectClient(url);
    const calls = consoleSpy.mock.calls.map((call) => String(call[0]));
    expect(calls.some((line) => line.includes("[ws] client connected"))).toBe(true);
  });
});
