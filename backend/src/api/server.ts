/**
 * REST API server (Phase 8, Step 8.4a + amendment A2).
 *
 * Six dashboard endpoints over the injectable `ApiStore` (PG) and
 * `CameraStatusStore` (Redis) — fastify's `inject` makes the whole
 * surface unit-testable without sockets or stores. Same-origin by
 * design: dev goes through the Vite proxy and compose through nginx
 * (phase-9 defaults), so no CORS plugin is registered — add one only
 * if a deployment ever serves the dashboard from a different origin.
 *
 * Auth is dropped by owner decision (2026-08-30): `actor` arrives in
 * the request body from the open single-user dashboard; every action
 * is audit-logged server-side regardless.
 */
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { resolve } from "node:path";
import Fastify, { type FastifyInstance, type FastifyReply, type FastifyRequest } from "fastify";
import { SEVERITIES } from "../lib/events.js";
import { decodeCursor } from "./store.js";
import type { ApiStore, CameraStatusStore } from "./store.js";

export interface ApiConfig {
  /** Absolute dir backing `/api/evidence/*` (the edge evidence root). Unset → 503. */
  evidenceRoot?: string;
}

const RFC3339 = /^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$/;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function createApiServer(
  store: ApiStore,
  cameraStatus: CameraStatusStore,
  config: ApiConfig = {}
): FastifyInstance {
  const app = Fastify({ logger: false });

  app.get<{ Querystring: Record<string, string | undefined> }>("/api/incidents", async (request, reply) => {
    const query = request.query;
    const severity = query["severity"];
    const since = query["since"];
    const until = query["until"];
    const limit = query["limit"] === undefined ? 50 : Number(query["limit"]);
    const acked = query["acked"] === undefined ? null : query["acked"] === "true";
    if (severity !== undefined && !(SEVERITIES as readonly string[]).includes(severity)) {
      return await reply.code(400).send({ error: `severity must be one of ${SEVERITIES.join(", ")}` });
    }
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
      return await reply.code(400).send({ error: "limit must be an integer in [1, 200]" });
    }
    if ((since !== undefined && !RFC3339.test(since)) || (until !== undefined && !RFC3339.test(until))) {
      return await reply.code(400).send({ error: "since/until must be RFC-3339 timestamps" });
    }
    if (query["acked"] !== undefined && query["acked"] !== "true" && query["acked"] !== "false") {
      return await reply.code(400).send({ error: "acked must be true or false" });
    }
    return await store.incidents({
      severity: severity ?? null,
      cameraId: query["camera"] ?? null,
      vehicleId: query["vehicle"] ?? null,
      since: since ?? null,
      until: until ?? null,
      acked,
      limit,
    });
  });

  app.get<{ Params: { id: string } }>("/api/incidents/:id", async (request, reply) => {
    const detail = await store.incident(request.params.id);
    if (detail === null) {
      return await reply.code(404).send({ error: "incident not found" });
    }
    return detail;
  });

  app.get("/api/cameras", async () => {
    const rows = await store.cameras();
    const status = await cameraStatus.online(rows.map((row) => row.cameraId));
    return rows.map((row) => ({ ...row, online: status[row.cameraId] === true }));
  });

  app.get<{ Querystring: { limit?: string; cursor?: string } }>("/api/events", async (request, reply) => {
    const limit = request.query.limit === undefined ? 50 : Number(request.query.limit);
    if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
      return await reply.code(400).send({ error: "limit must be an integer in [1, 200]" });
    }
    const raw = request.query.cursor;
    const cursor = raw === undefined ? null : decodeCursor(raw);
    if (raw !== undefined && cursor === null) {
      return await reply.code(400).send({ error: "malformed cursor" });
    }
    return await store.events(cursor, limit);
  });

  async function action(
    request: FastifyRequest<{ Params: { id: string } }>,
    reply: FastifyReply,
    run: (id: string, actor: string, detail: unknown) => Promise<{ updated: boolean }>
  ): Promise<unknown> {
    const body = request.body;
    const actor = isPlainObject(body) ? body["actor"] : undefined;
    if (typeof actor !== "string" || actor.length === 0) {
      return reply.code(400).send({ error: "body.actor (non-empty string) is required" });
    }
    const detail = isPlainObject(body) ? body["detail"] : undefined;
    const result = await run(request.params.id, actor, detail);
    if (!result.updated) {
      return reply.code(404).send({ error: "incident not found" });
    }
    return { ok: true };
  }

  app.post<{ Params: { id: string } }>("/api/incidents/:id/ack", async (request, reply) =>
    action(request, reply, (id, actor) => store.acknowledge(id, actor))
  );

  app.post<{ Params: { id: string } }>("/api/incidents/:id/escalate", async (request, reply) =>
    action(request, reply, (id, actor, detail) => store.escalate(id, actor, detail))
  );

  if (config.evidenceRoot !== undefined) {
    const root = resolve(config.evidenceRoot);
    app.get<{ Params: { "*": string } }>("/api/evidence/*", async (request, reply) => {
      const relative = request.params["*"];
      // resolve + prefix check: the traversal sandbox (A2's security clause)
      const absolute = resolve(root, relative);
      if (absolute !== root && !absolute.startsWith(`${root}/`)) {
        return await reply.code(403).send({ error: "evidence path escapes the root" });
      }
      const info = await stat(absolute).catch(() => null);
      if (info === null || !info.isFile()) {
        return await reply.code(404).send({ error: "evidence clip not found" });
      }
      const size = info.size;
      const range = parseRange(request.headers.range, size);
      if (range === "unsatisfiable") {
        return await reply.code(416).header("content-range", `bytes */${size}`).send({ error: "range not satisfiable" });
      }
      if (range === null) {
        return await reply
          .code(200)
          .header("accept-ranges", "bytes")
          .type("video/mp4")
          .send(createReadStream(absolute));
      }
      // 206 keeps Safari's <video> element happy (9.4 replay)
      return await reply
        .code(206)
        .header("content-range", `bytes ${range.start}-${range.end}/${size}`)
        .header("accept-ranges", "bytes")
        .type("video/mp4")
        .send(createReadStream(absolute, { start: range.start, end: range.end }));
    });
  } else {
    app.get("/api/evidence/*", async (_request, reply) =>
      reply.code(503).send({ error: "EVIDENCE_ROOT is not configured" })
    );
  }

  return app;
}

/** null = no/ignored Range header (full 200 is legal); "unsatisfiable" = 416. */
function parseRange(header: string | undefined, size: number): { start: number; end: number } | null | "unsatisfiable" {
  if (header === undefined || !header.startsWith("bytes=") || header.includes(",")) {
    return null;
  }
  const spec = header.slice("bytes=".length);
  const match = /^(\d*)-(\d*)$/.exec(spec);
  if (match === null || (match[1] === "" && match[2] === "")) {
    return null;
  }
  if (match[1] === "") {
    const suffix = Number(match[2]);
    if (suffix === 0) {
      return "unsatisfiable";
    }
    return { start: Math.max(0, size - suffix), end: size - 1 };
  }
  const start = Number(match[1]);
  if (start >= size) {
    return "unsatisfiable";
  }
  const requestedEnd = match[2] === "" ? size - 1 : Number(match[2]);
  return { start, end: Math.min(requestedEnd, size - 1) };
}
