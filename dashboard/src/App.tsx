/**
 * App shell: header (connection status + vehicle selector) and the four
 * locked layout regions. 9.1b put live data in the feed region; the
 * other three are still placeholders by design (9.2/9.3/9.5).
 *
 * ONE ws client per app mount feeds everything — the connection badge
 * and the feed ride the same socket (a second socket would report
 * health for a connection nobody listens on). Degrade, never crash:
 * with no backend the shell still renders.
 */
import { useEffect, useState } from "react";
import { listCameras } from "./api/client";
import type { CameraView } from "./domain";
import FeedPanel from "./feed/FeedPanel";
import { createDashboardWs, type ConnectionState, type DashboardWsClient } from "./ws/client";

const STATUS_STYLES: Record<ConnectionState, { dot: string; label: string }> = {
  connecting: { dot: "bg-sky-400", label: "CONNECTING" },
  connected: { dot: "bg-emerald-400", label: "CONNECTED" },
  reconnecting: { dot: "bg-amber-400 animate-pulse", label: "RECONNECTING" },
  offline: { dot: "bg-red-500", label: "OFFLINE" },
};

interface PanelProps {
  title: string;
  step: string;
  hint: string;
  testId: string;
  className?: string;
}

function Panel({ title, step, hint, testId, className = "" }: PanelProps) {
  return (
    <section
      data-testid={testId}
      className={`flex min-h-0 flex-col rounded-md border border-zinc-800 bg-zinc-900/60 ${className}`}
    >
      <header className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
        <h2 className="text-xs font-medium tracking-widest text-zinc-400 uppercase">{title}</h2>
        <span className="font-mono text-[10px] tracking-widest text-zinc-500">{step}</span>
      </header>
      <div className="flex flex-1 items-center justify-center p-4">
        <p className="max-w-56 text-center text-xs text-zinc-500">{hint}</p>
      </div>
    </section>
  );
}

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [cameras, setCameras] = useState<CameraView[]>([]);
  const [registryOffline, setRegistryOffline] = useState(false);
  const [selectedVehicle, setSelectedVehicle] = useState("");
  const [ws, setWs] = useState<DashboardWsClient | null>(null);

  useEffect(() => {
    const client: DashboardWsClient = createDashboardWs();
    const unsubscribe = client.onConnectionChange(setConnection);
    client.connect();
    setWs(client);
    return () => {
      unsubscribe();
      client.close();
      setWs(null);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    listCameras()
      .then((rows) => {
        if (!cancelled) {
          setCameras(rows);
          setRegistryOffline(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCameras([]);
          setRegistryOffline(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const vehicles = [...new Set(cameras.map((camera) => camera.vehicleId))].sort();

  async function onVehicleChange(vehicleId: string): Promise<void> {
    setSelectedVehicle(vehicleId);
    const joined = await ws?.subscribeVehicle(vehicleId);
    if (joined === false) {
      console.warn(`[shell] subscribe not acked for ${vehicleId}`);
    }
  }

  const status = STATUS_STYLES[connection];

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-zinc-800 bg-zinc-950 px-4 py-3">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-sm font-semibold tracking-[0.2em] text-zinc-100 uppercase">
            MobiSentra
          </span>
          <span className="text-xs tracking-widest text-zinc-500 uppercase">Control Center</span>
        </div>
        <div className="flex items-center gap-4">
          {registryOffline && (
            <span className="font-mono text-[10px] tracking-widest text-red-400 uppercase">
              registry offline
            </span>
          )}
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            <span className="tracking-widest uppercase">Vehicle</span>
            <select
              data-testid="vehicle-select"
              value={selectedVehicle}
              onChange={(event) => {
                void onVehicleChange(event.target.value);
              }}
              disabled={vehicles.length === 0}
              className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-xs text-zinc-200 disabled:opacity-50"
            >
              {vehicles.length === 0 && <option value="">— no vehicles —</option>}
              {vehicles.map((vehicleId) => (
                <option key={vehicleId} value={vehicleId}>
                  {vehicleId}
                </option>
              ))}
            </select>
          </label>
          <div
            data-testid="conn-status"
            className="flex items-center gap-2 rounded border border-zinc-800 bg-zinc-900/60 px-2 py-1"
          >
            <span className={`h-2 w-2 rounded-full ${status.dot}`} aria-hidden="true" />
            <span className="font-mono text-[10px] tracking-widest text-zinc-300">
              {status.label}
            </span>
          </div>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[2fr_3fr] lg:grid-rows-[minmax(0,3fr)_minmax(0,3fr)_minmax(0,2fr)]">
        <FeedPanel
          className="lg:row-span-2"
          vehicleId={selectedVehicle}
          ws={ws}
        />
        <Panel
          testId="region-cameras"
          title="Camera Grid"
          step="9.2"
          hint="Cameras with online status and occupancy badges per vehicle."
        />
        <Panel
          testId="region-incidents"
          title="Active Incidents"
          step="9.3"
          hint="Newest-first incidents with acknowledge and escalate actions."
        />
        <Panel
          className="lg:col-span-2"
          testId="region-history"
          title="Event History"
          step="9.5"
          hint="Filterable, cursor-paged history of all events."
        />
      </main>
    </div>
  );
}
