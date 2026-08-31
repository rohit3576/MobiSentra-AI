/**
 * Feed row model + presentation maps (9.1b).
 *
 * The feed unifies two wire shapes: REST history (`IncidentSummary`, from
 * GET /api/incidents) and live push (`EventRecord`, the Socket.IO "event"
 * channel). Both collapse to FeedRow; the id (event id) is the dedupe key
 * because a live event can race the initial history load.
 */
import type { EventRecord, IncidentSummary } from "../domain";

export interface FeedRow {
  id: string;
  eventType: string;
  severity: string;
  cameraId: string;
  /** ISO timestamp from the wire (edge clock). */
  occurredAt: string;
}

export function rowFromIncident(incident: IncidentSummary): FeedRow {
  return {
    id: incident.eventId,
    eventType: incident.eventType,
    severity: incident.severity.toUpperCase(),
    cameraId: incident.cameraId,
    occurredAt: incident.occurredAt,
  };
}

export function rowFromEvent(record: EventRecord): FeedRow {
  return {
    id: record.id,
    eventType: record.eventType,
    severity: record.severity.toUpperCase(),
    cameraId: record.cameraId,
    occurredAt: record.occurredAt,
  };
}

export interface SeverityStyle {
  border: string;
  badge: string;
}

/** Literal class strings so Tailwind's scanner sees every variant. */
const CRITICAL: SeverityStyle = {
  border: "border-l-red-500",
  badge: "border border-red-500/40 bg-red-500/15 text-red-300",
};
const HIGH: SeverityStyle = {
  border: "border-l-orange-500",
  badge: "border border-orange-500/40 bg-orange-500/15 text-orange-300",
};
const MEDIUM: SeverityStyle = {
  border: "border-l-amber-400",
  badge: "border border-amber-400/40 bg-amber-400/15 text-amber-200",
};
const LOW: SeverityStyle = {
  border: "border-l-sky-500",
  badge: "border border-sky-500/40 bg-sky-500/15 text-sky-300",
};
const UNKNOWN: SeverityStyle = {
  border: "border-l-zinc-600",
  badge: "border border-zinc-500/40 bg-zinc-500/15 text-zinc-300",
};

export function severityStyle(severity: string): SeverityStyle {
  switch (severity.toUpperCase()) {
    case "CRITICAL":
      return CRITICAL;
    case "HIGH":
      return HIGH;
    case "MEDIUM":
      return MEDIUM;
    case "LOW":
      return LOW;
    default:
      return UNKNOWN;
  }
}

const clock = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** Local wall-clock time for the row's right edge; full ISO on the title. */
export function formatClock(occurredAt: string): string {
  const date = new Date(occurredAt);
  return Number.isNaN(date.getTime()) ? occurredAt : clock.format(date);
}
