/**
 * Typed REST client over the Phase-8 backend API (9.1a).
 *
 * Base is same-origin `/api` — in dev the Vite proxy forwards to the
 * backend on :3000, in compose nginx does the same. No CORS, no retry
 * logic: a failed call surfaces as a rejected promise and the caller
 * decides (shell policy: degrade, never crash).
 */
import type {
  CameraView,
  EventHistoryPage,
  IncidentDetail,
  IncidentQueryFilters,
  IncidentSummary,
} from "../domain";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, path: string) {
    super(`API ${status} on ${path}`);
    this.name = "ApiError";
    this.status = status;
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    throw new ApiError(response.status, path);
  }
  // JSON is untyped at the parse boundary; the backend DTO contract
  // (src/domain.ts) is the trust boundary — this is the single cast.
  const body: unknown = await response.json();
  return body as T;
}

function buildQuery(filters: IncidentQueryFilters): string {
  const params = new URLSearchParams();
  if (filters.severity !== undefined) params.set("severity", filters.severity);
  if (filters.camera !== undefined) params.set("camera", filters.camera);
  if (filters.vehicle !== undefined) params.set("vehicle", filters.vehicle);
  if (filters.since !== undefined) params.set("since", filters.since);
  if (filters.until !== undefined) params.set("until", filters.until);
  if (filters.acked !== undefined) params.set("acked", String(filters.acked));
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  const query = params.toString();
  return query.length > 0 ? `?${query}` : "";
}

export function listIncidents(filters: IncidentQueryFilters = {}): Promise<IncidentSummary[]> {
  return json<IncidentSummary[]>(`/api/incidents${buildQuery(filters)}`);
}

export function getIncident(id: string): Promise<IncidentDetail> {
  return json<IncidentDetail>(`/api/incidents/${encodeURIComponent(id)}`);
}

export function listCameras(): Promise<CameraView[]> {
  return json<CameraView[]>("/api/cameras");
}

export function listEvents(cursor: string | null, limit: number): Promise<EventHistoryPage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor !== null) params.set("cursor", cursor);
  return json<EventHistoryPage>(`/api/events?${params.toString()}`);
}

export function ackIncident(id: string, actor: string): Promise<{ updated: boolean }> {
  return json<{ updated: boolean }>(`/api/incidents/${encodeURIComponent(id)}/ack`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ actor }),
  });
}

export function escalateIncident(
  id: string,
  actor: string,
  detail?: unknown
): Promise<{ updated: boolean }> {
  return json<{ updated: boolean }>(`/api/incidents/${encodeURIComponent(id)}/escalate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(detail === undefined ? { actor } : { actor, detail }),
  });
}
