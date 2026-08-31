/**
 * Typed Socket.IO client for the Phase-8 push server (9.1a).
 *
 * Wire contract (backend/src/ws/push.ts):
 *   client → "subscribe" (vehicleId, ack(joined)) — one call joins BOTH
 *   `alerts:{vehicle}` (→ "event", EventRecord) and `cameras:{vehicle}`
 *   (→ "state", CameraState).
 *
 * The classic trap this client exists to kill: socket.io reconnects the
 * transport but NOT room memberships — a silent dead feed. So every
 * "connect" (initial or reconnect) re-subscribes the active vehicle and
 * awaits the server ack before the subscription is considered live.
 * Proven by ws/client.test.ts (in-process server, forced disconnect).
 */
import { io, type ManagerOptions, type Socket, type SocketOptions } from "socket.io-client";
import type { CameraState, EventRecord } from "../domain";

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "offline";

export interface WsClientOptions {
  /** Server origin — omit for same-origin (Vite dev proxy / compose nginx). */
  url?: string;
  /** Backoff tuning; tests pass small values for fast reconnects. */
  reconnectionDelay?: number;
  reconnectionDelayMax?: number;
  /** Deadline for the subscribe ack (default 5 s). */
  ackTimeoutMs?: number;
}

type Unsubscribe = () => void;

export interface DashboardWsClient {
  connect(): void;
  close(): void;
  /** Join the vehicle's rooms; resolves when the server acks the join. */
  subscribeVehicle(vehicleId: string): Promise<boolean>;
  onEvent(callback: (record: EventRecord) => void): Unsubscribe;
  onState(callback: (state: CameraState) => void): Unsubscribe;
  onConnectionChange(callback: (state: ConnectionState) => void): Unsubscribe;
}

export function createDashboardWs(options: WsClientOptions = {}): DashboardWsClient {
  const ackTimeoutMs = options.ackTimeoutMs ?? 5_000;

  let socket: Socket | null = null;
  let subscribedVehicle: string | null = null;

  const eventListeners: Array<(record: EventRecord) => void> = [];
  const stateListeners: Array<(state: CameraState) => void> = [];
  const connectionListeners: Array<(state: ConnectionState) => void> = [];

  function setConnection(state: ConnectionState): void {
    for (const listener of [...connectionListeners]) {
      listener(state);
    }
  }

  function emitSubscribe(vehicleId: string): Promise<boolean> {
    const active = socket;
    if (active === null) {
      return Promise.resolve(false);
    }
    // Each call resolves from its own ack (or its own timeout) — a late
    // ack after a vehicle switch only ever resolves the promise it belongs
    // to; the reconnect barrier re-emits for the CURRENT vehicle anyway.
    return new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => resolve(false), ackTimeoutMs);
      active.emit("subscribe", vehicleId, (joined: boolean) => {
        clearTimeout(timer);
        resolve(joined);
      });
    });
  }

  function ensureSocket(): Socket {
    if (socket !== null) {
      return socket;
    }
    const socketOptions: Partial<ManagerOptions & SocketOptions> = {
      // builtin reconnect with backoff — always ON (plan-locked)
      reconnectionDelay: options.reconnectionDelay ?? 500,
      reconnectionDelayMax: options.reconnectionDelayMax ?? 5_000,
    };
    // socket.io-client 4.8's entrypoint is non-generic — wire types are
    // carried by the annotated handlers below; the backend is the contract.
    socket = options.url === undefined ? io(socketOptions) : io(options.url, socketOptions);

    socket.on("connect", () => {
      setConnection("connected");
      // Re-subscribe barrier: reconnect wipes room memberships. The
      // ack'd re-join is fire-and-forget externally (callers already
      // hold a resolved subscribeVehicle promise) but never skipped.
      if (subscribedVehicle !== null) {
        void emitSubscribe(subscribedVehicle).then((joined) => {
          if (!joined) {
            console.warn(`[ws] re-subscribe after reconnect not acked (${subscribedVehicle})`);
          }
        });
      }
    });
    socket.on("disconnect", () => {
      // active = the manager will retry (reconnecting); inactive = we
      // closed it or the server refused (offline).
      const state: ConnectionState = socket?.active === true ? "reconnecting" : "offline";
      setConnection(state);
    });
    socket.on("event", (record: EventRecord) => {
      for (const listener of [...eventListeners]) {
        listener(record);
      }
    });
    socket.on("state", (state: CameraState) => {
      for (const listener of [...stateListeners]) {
        listener(state);
      }
    });
    return socket;
  }

  return {
    connect() {
      setConnection("connecting");
      ensureSocket().connect();
    },
    close() {
      // Drops the active vehicle (no re-subscribe on a later re-open)
      // and stops reconnection; connect() can still re-open the socket.
      subscribedVehicle = null;
      socket?.close();
      setConnection("offline");
    },
    async subscribeVehicle(vehicleId: string) {
      const trimmed = vehicleId.trim();
      if (trimmed.length === 0) {
        return false;
      }
      subscribedVehicle = trimmed;
      return emitSubscribe(trimmed);
    },
    onEvent(callback) {
      eventListeners.push(callback);
      return () => {
        const index = eventListeners.indexOf(callback);
        if (index >= 0) {
          eventListeners.splice(index, 1);
        }
      };
    },
    onState(callback) {
      stateListeners.push(callback);
      return () => {
        const index = stateListeners.indexOf(callback);
        if (index >= 0) {
          stateListeners.splice(index, 1);
        }
      };
    },
    onConnectionChange(callback) {
      connectionListeners.push(callback);
      return () => {
        const index = connectionListeners.indexOf(callback);
        if (index >= 0) {
          connectionListeners.splice(index, 1);
        }
      };
    },
  };
}
