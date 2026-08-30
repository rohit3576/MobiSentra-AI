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
import { validateEnvelope, validateEventData } from "../schema/events.js";

export const SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type Severity = (typeof SEVERITIES)[number];

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
  raw: unknown;
}

export type MappingResult =
  | { ok: true; record: EventRecord }
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
      raw: envelope,
    },
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
