/**
 * Live incident feed (9.1b).
 *
 * One panel, two data paths, one list:
 *   - history: GET /api/incidents?vehicle={selected} on every vehicle
 *     switch (backend order: occurred_at DESC — newest first)
 *   - live: Socket.IO "event" pushes for the selected vehicle,
 *     prepended at the top
 * The event id is the dedupe key — a push that races (or predates) the
 * history load must not double-render.
 *
 * Wire-contract quirk this guards against: the backend's `subscribe`
 * joins the new vehicle's rooms but never leaves the previous ones, so
 * after a vehicle switch the socket still receives the OLD vehicle's
 * events. Every push is therefore filtered by its own vehicleId, not
 * trusted by room membership.
 *
 * Reconnects never touch rows: state lives here, the transport lives in
 * the ws client — a drop freezes the list, the re-subscribe barrier
 * (ws/client.ts) resumes it. Proven by FeedPanel.reconnect.test.tsx.
 */
import { useEffect, useState } from "react";
import { listIncidents } from "../api/client";
import type { EventRecord } from "../domain";
import type { DashboardWsClient } from "../ws/client";
import { formatClock, rowFromEvent, rowFromIncident, severityStyle, type FeedRow } from "./rows";

const HISTORY_LIMIT = 50;
const MAX_ROWS = 200;

type LoadState = "idle" | "loading" | "ready" | "unavailable";

interface FeedPanelProps {
  /** Selected vehicle; "" = no vehicle chosen yet. */
  vehicleId: string;
  /** The shell's single WS client; null before the socket effect ran. */
  ws: DashboardWsClient | null;
  className?: string;
}

export default function FeedPanel({ vehicleId, ws, className = "" }: FeedPanelProps) {
  const [rows, setRows] = useState<FeedRow[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("idle");

  // History load per vehicle. A switch resets the list (fresh vehicle,
  // fresh history); live rows seen while the fetch was in flight are
  // kept on top — they are by definition newer than the snapshot.
  useEffect(() => {
    if (vehicleId === "") {
      setRows([]);
      setLoadState("idle");
      return;
    }
    let cancelled = false;
    // reset before fetch: the merge below must never keep the previous vehicle's rows
    setRows([]);
    setLoadState("loading");
    listIncidents({ vehicle: vehicleId, limit: HISTORY_LIMIT })
      .then((incidents) => {
        if (cancelled) {
          return;
        }
        setRows((previous) => {
          const history = incidents.map(rowFromIncident);
          const historyIds = new Set(history.map((row) => row.id));
          const racedLive = previous.filter((row) => !historyIds.has(row.id));
          return [...racedLive, ...history];
        });
        setLoadState("ready");
      })
      .catch(() => {
        // Degrade, never crash: history is gone, live prepend still works.
        if (!cancelled) {
          setLoadState("unavailable");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [vehicleId]);

  // Live path. Re-armed whenever the socket identity or the vehicle
  // changes; the push's own vehicleId is the filter (rooms never leave).
  useEffect(() => {
    if (ws === null || vehicleId === "") {
      return;
    }
    const unsubscribe = ws.onEvent((record: EventRecord) => {
      if (record.vehicleId !== vehicleId) {
        return;
      }
      setRows((previous) => {
        if (previous.some((row) => row.id === record.id)) {
          return previous;
        }
        return [rowFromEvent(record), ...previous].slice(0, MAX_ROWS);
      });
    });
    return unsubscribe;
  }, [ws, vehicleId]);

  const emptyHint =
    vehicleId === ""
      ? "Select a vehicle to open its alert feed."
      : loadState === "loading"
        ? "loading incident history…"
        : rows.length === 0
          ? "no incidents yet — live feed armed"
          : null;

  return (
    <section
      data-testid="region-feed"
      className={`flex min-h-0 flex-col rounded-md border border-zinc-800 bg-zinc-900/60 ${className}`}
    >
      <header className="flex items-center justify-between gap-2 border-b border-zinc-800 px-3 py-2">
        <div className="flex items-baseline gap-2">
          <h2 className="text-xs font-medium tracking-widest text-zinc-400 uppercase">Live Feed</h2>
          <span className="font-mono text-[10px] text-zinc-500">
            {vehicleId === "" ? "—" : vehicleId}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {loadState === "unavailable" && (
            <span
              data-testid="feed-degraded"
              className="font-mono text-[10px] tracking-widest text-amber-300/90 uppercase"
            >
              history unavailable
            </span>
          )}
          <span
            data-testid="feed-count"
            className="font-mono text-[10px] tracking-widest text-zinc-500"
          >
            {rows.length}
          </span>
        </div>
      </header>

      {emptyHint !== null ? (
        <div className="flex flex-1 items-center justify-center p-4">
          <p className="max-w-56 text-center text-xs text-zinc-500">{emptyHint}</p>
        </div>
      ) : (
        <ul data-testid="feed-list" className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
          {rows.map((row) => {
            const style = severityStyle(row.severity);
            return (
              <li
                key={row.id}
                data-testid="feed-row"
                data-event-id={row.id}
                title={row.occurredAt}
                className={`mb-1.5 flex items-center gap-3 rounded-r-sm border-l-2 bg-zinc-950/60 px-3 py-2 ${style.border}`}
              >
                <span
                  className={`shrink-0 rounded-sm px-1.5 py-0.5 font-mono text-[10px] font-semibold tracking-wider ${style.badge}`}
                >
                  {row.severity}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-200">
                  {row.eventType}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-zinc-500">{row.cameraId}</span>
                <span className="shrink-0 font-mono text-[10px] text-zinc-400 tabular-nums">
                  {formatClock(row.occurredAt)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
