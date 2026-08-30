/**
 * Socket.IO push server (Phase 8, Step 8.3c).
 *
 * One room per vehicle, `alerts:{vehicle_id}` (runbook shape; camera
 * rooms ride the vehicle room via source parsing). The 8.3b pipeline
 * holds only the `EventPusher` slice — an in-memory fake in its unit
 * tests, this server in production.
 *
 * Subscribe is the trust boundary: client input is guarded (non-empty
 * string) and confirmed with a Socket.IO ack callback — the ack gives
 * tests (and the Phase-9 dashboard) a deterministic join barrier. CORS
 * defaults open for local dev; Phase 9 pins `corsOrigins` to the
 * dashboard origin instead of running a second allow-all server.
 */
import type { Server as HttpServer } from "node:http";
import { Server } from "socket.io";
import type { CameraState, EventRecord } from "../lib/events.js";

/** What the 8.3b pipeline depends on — Socket.IO never leaks past this. */
export interface EventPusher {
  publish(vehicleId: string, event: EventRecord): void;
  publishState(vehicleId: string, state: CameraState): void;
}

export interface PushServerOptions {
  /** Socket.IO endpoint path (default "/socket.io"). */
  path?: string;
  /** Allowed browser origins (default: reflect request origin — dev only). */
  corsOrigins?: string[];
}

export interface PushHandle {
  readonly pusher: EventPusher;
  close(): Promise<void>;
}

export function vehicleRoom(vehicleId: string): string {
  return `alerts:${vehicleId}`;
}

export function cameraStateRoom(vehicleId: string): string {
  return `cameras:${vehicleId}`;
}

export function createPushServer(httpServer: HttpServer, options: PushServerOptions = {}): PushHandle {
  const io = new Server(httpServer, {
    path: options.path ?? "/socket.io",
    cors: { origin: options.corsOrigins ?? true },
  });

  io.on("connection", (socket) => {
    console.log(`[ws] client connected (${socket.id})`);
    socket.on("subscribe", (vehicleId: unknown, ack?: (joined: boolean) => void) => {
      if (typeof vehicleId !== "string" || vehicleId.length === 0) {
        ack?.(false);
        return;
      }
      // one subscribe, both channels: a vehicle watcher wants its alerts
      // AND its camera occupancy state (A1)
      socket.join(vehicleRoom(vehicleId));
      socket.join(cameraStateRoom(vehicleId));
      console.log(`[ws] ${socket.id} subscribed ${vehicleRoom(vehicleId)} + ${cameraStateRoom(vehicleId)}`);
      ack?.(true);
    });
    socket.on("disconnect", (reason) => {
      console.log(`[ws] client disconnected (${socket.id}, ${reason})`);
    });
  });

  return {
    pusher: {
      publish: (vehicleId, event) => {
        io.to(vehicleRoom(vehicleId)).emit("event", event);
      },
      publishState: (vehicleId, state) => {
        io.to(cameraStateRoom(vehicleId)).emit("state", state);
      },
    },
    close: () => io.close(),
  };
}
