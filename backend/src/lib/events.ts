/**
 * Envelope → typed event record (Phase 8, Step 8.1a).
 *
 * The consumer's single parse point: ajv validates against the shared
 * schemas FIRST (the Phase-0 contract freeze), then fields are extracted
 * with runtime type guards — no casts, so a schema/code drift fails loudly
 * here instead of surfacing as `undefined` deep in the pipeline.
 *
 * `source` parsing is deliberately lenient: the envelope schema only
 * demands the `/mobisentra/` prefix, so `/mobisentra/edge/{vehicle}/{camera}`
 * is the expected shape but anything else degrades to vehicle `unknown` /
 * camera from `data.camera_id` — a malformed source is a routing problem,
 * never a reason to drop a safety event.
 */
import { validateAnalyticsData, validateEnvelope, validateEventData } from "../schema/events.js";

export const SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type Severity = (typeof SEVERITIES)[number];

export const ANALYTICS_TYPE_PREFIX = "org.mobisentra.analytics.";
export type EnvelopeKind = "safety" | "analytics";

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
  /** Occupancy diagnostics when this safety event is a band flip (A1 live path). */
  occupancy: OccupancyInfo | null;
  raw: unknown;
}

export type MappingResult =
  | { ok: true; record: EventRecord }
  | { ok: false; errors: string[] };

/**
 * Occupancy snapshot shared by both A1 paths: safety events carrying
 * to_band/count/ratio diagnostics, and analytics envelopes (level comes
 * from the edge's band computation — the dashboard displays, never
 * re-derives).
 */
export interface CameraState {
  cameraId: string;
  zone: string | null;
  level: string | null;
  peopleCount: number | null;
  ratio: number | null;
  ts: string;
}

/** Occupancy fields extracted from an occupancy-kind safety event's data. */
export interface OccupancyInfo {
  zone: string | null;
  level: string;
  peopleCount: number | null;
  ratio: number | null;
}

export interface AnalyticsState {
  vehicleId: string;
  cameraId: string;
  zone: string;
  level: string;
  peopleCount: number;
  occupancyRatio: number | null;
  occurredAt: string;
}

export type AnalyticsMappingResult =
  | { ok: true; state: AnalyticsState }
  | { ok: false; errors: string[] };

const UNKNOWN = "unknown";

export function toRecord(envelope: unknown): MappingResult {
  const errors: string[] = [];
  const envelopeCheck = validateEnvelope(envelope);
  errors.push(...envelopeCheck.errors);
  const data = field(envelope, "data");
  if (data === null || typeof data !== "object") {
    return { ok: false, errors: errors.length > 0 ? errors : ["envelope.data missing"] };
  }
  const dataCheck = validateEventData(data);
  errors.push(...dataCheck.errors);
  if (errors.length > 0) {
    return { ok: false, errors };
  }

  const id = stringField(envelope, "id");
  const source = stringField(envelope, "source");
  const eventType = stringField(data, "event_type");
  const severityRaw = stringField(data, "severity");
  const occurredAt = stringField(data, "timestamp");
  const cameraFromData = stringField(data, "camera_id");
  if (id === null || source === null || eventType === null || occurredAt === null) {
    return { ok: false, errors: [...errors, "required field extraction failed (validated envelope diverged from code expectations)"] };
  }
  if (severityRaw === null || !isSeverity(severityRaw)) {
    return { ok: false, errors: [...errors, `severity ${String(severityRaw)} not a known level`] };
  }
  const { vehicleId, cameraId } = parseSource(source, cameraFromData);

  return {
    ok: true,
    record: {
      id,
      source,
      vehicleId,
      cameraId,
      eventType,
      severity: severityRaw,
      occurredAt,
      tracks: numberArrayField(data, "tracks"),
      location: stringField(data, "location"),
      evidenceRef: stringField(data, "evidence_ref"),
      modelVersions: stringRecordField(data, "model_versions"),
      occupancy: occupancyInfo(data),
      raw: envelope,
    },
  };
}

/** Routes an envelope to its pipeline branch by CloudEvents `type` prefix. */
export function envelopeKind(envelope: unknown): EnvelopeKind {
  const type = stringField(envelope, "type");
  return type !== null && type.startsWith(ANALYTICS_TYPE_PREFIX) ? "analytics" : "safety";
}

/** Analytics envelope → live-state snapshot (A1 contract path — Redis + WS only). */
export function toAnalyticsState(envelope: unknown): AnalyticsMappingResult {
  const errors: string[] = [];
  errors.push(...validateEnvelope(envelope).errors);
  const data = field(envelope, "data");
  if (data === null || typeof data !== "object") {
    return { ok: false, errors: errors.length > 0 ? errors : ["envelope.data missing"] };
  }
  errors.push(...validateAnalyticsData(data).errors);
  if (errors.length > 0) {
    return { ok: false, errors };
  }
  const source = stringField(envelope, "source");
  const zone = stringField(data, "zone");
  const level = stringField(data, "level");
  const occurredAt = stringField(data, "ts");
  const cameraFromData = stringField(data, "camera_id");
  const peopleCount = numberField(data, "people_count");
  if (source === null || zone === null || level === null || occurredAt === null || peopleCount === null) {
    return { ok: false, errors: [...errors, "required field extraction failed (validated analytics envelope diverged from code expectations)"] };
  }
  const { vehicleId, cameraId } = parseSource(source, cameraFromData);
  return {
    ok: true,
    state: { vehicleId, cameraId, zone, level, peopleCount, occupancyRatio: numberField(data, "occupancy_ratio"), occurredAt },
  };
}

/** Occupancy diagnostics ride as additional data properties on band-flip events. */
function occupancyInfo(data: unknown): OccupancyInfo | null {
  const level = stringField(data, "to_band");
  if (level === null) {
    return null;
  }
  return {
    zone: stringField(data, "zone"),
    level,
    peopleCount: numberField(data, "count"),
    ratio: numberField(data, "ratio"),
  };
}

function parseSource(
  source: string,
  cameraFromData: string | null
): { vehicleId: string; cameraId: string } {
  const segments = source.split("/").filter((segment) => segment.length > 0);
  const edgeIndex = segments.indexOf("edge");
  const afterEdge = edgeIndex >= 0 ? segments.slice(edgeIndex + 1) : [];
  // data.camera_id IS the edge camera-registry key (what the API queries);
  // the source path segment is only a fallback when data lacked it
  const vehicleId = afterEdge[0] ?? UNKNOWN;
  const cameraId = cameraFromData ?? afterEdge[1] ?? UNKNOWN;
  return { vehicleId, cameraId };
}

function isSeverity(value: string): value is Severity {
  return (SEVERITIES as readonly string[]).includes(value);
}

function field(object: unknown, key: string): unknown {
  if (object !== null && typeof object === "object" && key in object) {
    return (object as Record<string, unknown>)[key];
  }
  return null;
}

function stringField(object: unknown, key: string): string | null {
  const value = field(object, key);
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberField(object: unknown, key: string): number | null {
  const value = field(object, key);
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function numberArrayField(object: unknown, key: string): number[] {
  const value = field(object, key);
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((entry): entry is number => typeof entry === "number");
}

function stringRecordField(object: unknown, key: string): Record<string, string> {
  const value = field(object, key);
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const result: Record<string, string> = {};
  for (const [entryKey, entryValue] of Object.entries(value)) {
    if (typeof entryValue === "string") {
      result[entryKey] = entryValue;
    }
  }
  return result;
}
