/**
 * Domain types mirrored 1:1 from the Phase-8 backend wire contract
 * (backend/src/lib/events.ts, backend/src/api/store.ts). The backend is
 * the source of truth; on drift, fix these to match it — never the
 * other way around.
 */

export const SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type Severity = (typeof SEVERITIES)[number];

/** Socket.IO `"event"` payload — one processed safety event (8.3c push). */
export interface EventRecord {
  id: string;
  source: string;
  vehicleId: string;
  cameraId: string;
  eventType: string;
  severity: Severity;
  occurredAt: string;
  tracks: number[];
  location: string | null;
  evidenceRef: string | null;
  modelVersions: Record<string, string>;
  occupancy: OccupancyInfo | null;
  raw: unknown;
}

/** Occupancy diagnostics riding on band-flip safety events. */
export interface OccupancyInfo {
  zone: string | null;
  level: string;
  peopleCount: number | null;
  ratio: number | null;
}

/** Socket.IO `"state"` payload — live camera occupancy snapshot (A1). */
export interface CameraState {
  cameraId: string;
  zone: string | null;
  level: string | null;
  peopleCount: number | null;
  ratio: number | null;
  ts: string;
}

/** GET /api/incidents row. */
export interface IncidentSummary {
  eventId: string;
  eventType: string;
  severity: string;
  cameraId: string;
  vehicleId: string;
  occurredAt: string;
  ackedAt: string | null;
}

/** GET /api/incidents/:id row. */
export interface IncidentDetail extends IncidentSummary {
  evidenceRef: string | null;
  escalation: unknown;
  ackedBy: string | null;
  envelope: unknown;
}

/** GET /api/cameras row (PG registry + Redis live status merged). */
export interface CameraView {
  cameraId: string;
  vehicleId: string;
  online: boolean;
}

/** GET /api/events row. */
export interface EventHistoryRow {
  eventId: string;
  eventType: string;
  occurredAt: string;
  envelope: unknown;
}

/** GET /api/events page (keyset cursor). */
export interface EventHistoryPage {
  events: EventHistoryRow[];
  next: string | null;
}

/** Query filters for GET /api/incidents (all optional server-side). */
export interface IncidentQueryFilters {
  severity?: string;
  camera?: string;
  vehicle?: string;
  since?: string;
  until?: string;
  acked?: boolean;
  limit?: number;
}
