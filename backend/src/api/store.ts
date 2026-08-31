/**
 * REST API stores (Phase 8, Step 8.4a).
 *
 * Injectable data access for the six dashboard endpoints — SQL stays
 * visible and parameterized (no ORM, house rule), every mutation that
 * the runbook calls an "action" (ack/escalate) writes its audit_log row
 * in the SAME transaction as the events update: an action without its
 * audit trail must be impossible.
 *
 * Re-ack is deliberately allowed (idempotent update + an audit row per
 * POST — the audit log records actions, not state transitions); the
 * escalation column is last-wins, audit_log keeps the history.
 */
import type { Redis } from "ioredis";
import { liveStateKey } from "../consumer/pipeline.js";

export interface PgExecutor {
  query(sql: string, values?: unknown[]): Promise<{ rows: unknown[]; rowCount: number | null }>;
}

export interface IncidentFilters {
  severity: string | null;
  cameraId: string | null;
  vehicleId: string | null;
  since: string | null;
  until: string | null;
  acked: boolean | null;
  limit: number;
}

export interface IncidentSummary {
  eventId: string;
  eventType: string;
  severity: string;
  cameraId: string;
  vehicleId: string;
  occurredAt: string;
  ackedAt: string | null;
}

export interface IncidentDetail extends IncidentSummary {
  evidenceRef: string | null;
  escalation: unknown;
  ackedBy: string | null;
  envelope: unknown;
}

export interface EventHistoryRow {
  eventId: string;
  eventType: string;
  occurredAt: string;
  envelope: unknown;
}

export interface EventHistoryPage {
  events: EventHistoryRow[];
  next: string | null;
}

export interface CameraRow {
  cameraId: string;
  vehicleId: string;
}

export interface ApiStore {
  incidents(filters: IncidentFilters): Promise<IncidentSummary[]>;
  incident(id: string): Promise<IncidentDetail | null>;
  events(cursor: { occurredAt: string; eventId: string } | null, limit: number): Promise<EventHistoryPage>;
  cameras(): Promise<CameraRow[]>;
  acknowledge(id: string, actor: string): Promise<{ updated: boolean }>;
  escalate(id: string, actor: string, detail: unknown): Promise<{ updated: boolean }>;
}

export interface CameraStatusStore {
  online(cameraIds: string[]): Promise<Record<string, boolean>>;
}

type PgRow = Record<string, unknown>;

function isRow(value: unknown): value is PgRow {
  return value !== null && typeof value === "object";
}

function str(row: PgRow, key: string): string | null {
  const value = row[key];
  return typeof value === "string" ? value : null;
}

function ts(row: PgRow, key: string): string | null {
  const value = row[key];
  return value instanceof Date ? value.toISOString() : typeof value === "string" ? value : null;
}

export function encodeCursor(row: { occurredAt: string; eventId: string }): string {
  return Buffer.from(JSON.stringify({ o: row.occurredAt, i: row.eventId }), "utf8").toString("base64url");
}

export function decodeCursor(raw: string): { occurredAt: string; eventId: string } | null {
  try {
    const parsed: unknown = JSON.parse(Buffer.from(raw, "base64url").toString("utf8"));
    if (!isRow(parsed)) {
      return null;
    }
    const occurredAt = str(parsed, "o");
    const eventId = str(parsed, "i");
    return occurredAt !== null && eventId !== null ? { occurredAt, eventId } : null;
  } catch {
    return null; // malformed base64/JSON → caller maps to 400
  }
}

export class PgApiStore implements ApiStore {
  constructor(private readonly exec: PgExecutor) {}

  async incidents(filters: IncidentFilters): Promise<IncidentSummary[]> {
    const result = await this.exec.query(
      `SELECT event_id, event_type, severity, camera_id, vehicle_id, occurred_at, acked_at
       FROM events
       WHERE ($1::text IS NULL OR severity = $1)
         AND ($2::text IS NULL OR camera_id = $2)
         AND ($3::text IS NULL OR vehicle_id = $3)
         AND ($4::timestamptz IS NULL OR occurred_at >= $4)
         AND ($5::timestamptz IS NULL OR occurred_at <= $5)
         AND ($6::boolean IS NULL OR $6 = (acked_at IS NOT NULL))
       ORDER BY occurred_at DESC, event_id DESC
       LIMIT $7::int`,
      [filters.severity, filters.cameraId, filters.vehicleId, filters.since, filters.until, filters.acked, filters.limit]
    );
    return result.rows.filter(isRow).map((row) => ({
      eventId: str(row, "event_id") ?? "",
      eventType: str(row, "event_type") ?? "",
      severity: str(row, "severity") ?? "",
      cameraId: str(row, "camera_id") ?? "",
      vehicleId: str(row, "vehicle_id") ?? "",
      occurredAt: ts(row, "occurred_at") ?? "",
      ackedAt: ts(row, "acked_at"),
    }));
  }

  async incident(id: string): Promise<IncidentDetail | null> {
    const result = await this.exec.query(
      `SELECT event_id, event_type, severity, camera_id, vehicle_id, occurred_at, acked_at, acked_by,
              escalation, envelope, envelope->'data'->>'evidence_ref' AS evidence_ref
       FROM events WHERE event_id = $1`,
      [id]
    );
    const row = result.rows.find(isRow);
    if (row === undefined) {
      return null;
    }
    return {
      eventId: str(row, "event_id") ?? "",
      eventType: str(row, "event_type") ?? "",
      severity: str(row, "severity") ?? "",
      cameraId: str(row, "camera_id") ?? "",
      vehicleId: str(row, "vehicle_id") ?? "",
      occurredAt: ts(row, "occurred_at") ?? "",
      ackedAt: ts(row, "acked_at"),
      ackedBy: str(row, "acked_by"),
      escalation: row["escalation"] ?? null,
      evidenceRef: str(row, "evidence_ref"),
      envelope: row["envelope"] ?? null,
    };
  }

  async events(cursor: { occurredAt: string; eventId: string } | null, limit: number): Promise<EventHistoryPage> {
    const result = await this.exec.query(
      `SELECT event_id, event_type, occurred_at, envelope
       FROM events
       WHERE ($1::timestamptz IS NULL OR (occurred_at, event_id) < ($1, $2::text))
       ORDER BY occurred_at DESC, event_id DESC
       LIMIT $3::int`,
      [cursor?.occurredAt ?? null, cursor?.eventId ?? null, limit + 1]
    );
    const rows = result.rows.filter(isRow).map((row) => ({
      eventId: str(row, "event_id") ?? "",
      eventType: str(row, "event_type") ?? "",
      occurredAt: ts(row, "occurred_at") ?? "",
      envelope: row["envelope"] ?? null,
    }));
    if (rows.length <= limit) {
      return { events: rows, next: null };
    }
    const page = rows.slice(0, limit);
    const last = page[page.length - 1];
    return last === undefined ? { events: page, next: null } : { events: page, next: encodeCursor(last) };
  }

  async cameras(): Promise<CameraRow[]> {
    const result = await this.exec.query(
      `SELECT camera_id, vehicle_id FROM events GROUP BY camera_id, vehicle_id ORDER BY camera_id`
    );
    return result.rows.filter(isRow).map((row) => ({
      cameraId: str(row, "camera_id") ?? "",
      vehicleId: str(row, "vehicle_id") ?? "",
    }));
  }

  /** Action + audit row in one transaction; ROLLBACK + rethrow on failure. */
  private async action(
    update: string,
    updateParams: unknown[],
    audit: { action: string; eventId: string; actor: string; detail?: unknown }
  ): Promise<{ updated: boolean }> {
    try {
      await this.exec.query("BEGIN");
      const result = await this.exec.query(update, updateParams);
      const updated = (result.rowCount ?? 0) > 0;
      if (!updated) {
        await this.exec.query("ROLLBACK"); // no event → no orphan audit row
        return { updated: false };
      }
      await this.exec.query(
        `INSERT INTO audit_log (action, event_id, actor, detail) VALUES ($1, $2, $3, $4::jsonb)`,
        [audit.action, audit.eventId, audit.actor, JSON.stringify(audit.detail ?? {})]
      );
      await this.exec.query("COMMIT");
      return { updated: true };
    } catch (err) {
      await this.exec.query("ROLLBACK").catch(() => undefined); // best-effort cleanup; original error is the signal
      throw err;
    }
  }

  acknowledge(id: string, actor: string): Promise<{ updated: boolean }> {
    return this.action(
      `UPDATE events SET acked_at = now(), acked_by = $2 WHERE event_id = $1`,
      [id, actor],
      { action: "ack", eventId: id, actor }
    );
  }

  escalate(id: string, actor: string, detail: unknown): Promise<{ updated: boolean }> {
    return this.action(
      // $2::text is load-bearing: jsonb_build_object gives PG no type to
      // infer from (live-caught 42P18 — unit fakes can't see this)
      `UPDATE events SET escalation = jsonb_build_object('actor', $2::text, 'at', now(), 'detail', $3::jsonb) WHERE event_id = $1`,
      [id, actor, JSON.stringify(detail ?? {})],
      { action: "escalate", eventId: id, actor, detail }
    );
  }
}

/** Online = the 8.3b live-state TTL is still alive (ttl > 0). */
export class RedisCameraStatus implements CameraStatusStore {
  constructor(private readonly client: Pick<Redis, "pipeline">) {}

  async online(cameraIds: string[]): Promise<Record<string, boolean>> {
    const result: Record<string, boolean> = {};
    if (cameraIds.length === 0) {
      return result;
    }
    const pipe = this.client.pipeline();
    for (const cameraId of cameraIds) {
      pipe.ttl(liveStateKey(cameraId));
    }
    const replies = await pipe.exec();
    cameraIds.forEach((cameraId, index) => {
      const reply = replies?.[index];
      const ttl = reply?.[1];
      result[cameraId] = typeof ttl === "number" && ttl > 0;
    });
    return result;
  }
}
