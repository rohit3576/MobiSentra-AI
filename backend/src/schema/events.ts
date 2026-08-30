/**
 * Event schema v0 loading + validation (Phase 0, Step 0.9).
 *
 * The schemas in /schemas/events/v0 are the single source of truth shared by
 * the edge pipeline and this backend. The consumer (Phase 8) validates every
 * incoming CloudEvents envelope before processing.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Ajv, type ErrorObject, type ValidateFunction } from "ajv";

const here = dirname(fileURLToPath(import.meta.url));
const schemasDir = join(here, "../../../schemas/events/v0");

export function loadSchema(name: string): object {
  const raw = JSON.parse(readFileSync(join(schemasDir, `${name}.schema.json`), "utf8")) as object;
  return raw;
}

export function loadExample(name: string): unknown {
  return JSON.parse(readFileSync(join(schemasDir, "examples", `${name}.json`), "utf8"));
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

function toResult(valid: boolean, errors: ErrorObject[] | null | undefined): ValidationResult {
  return {
    valid,
    errors: (errors ?? []).map((e) => `${e.instancePath} ${e.message ?? "invalid"}`),
  };
}

const RFC3339_DATETIME =
  /^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$/;

export function createAjv(): Ajv {
  const ajv = new Ajv({ allErrors: true });
  ajv.addFormat("date-time", RFC3339_DATETIME);
  return ajv;
}

const ajv = createAjv();
const envelopeValidator: ValidateFunction = ajv.compile(loadSchema("envelope"));

/** Validate a CloudEvents envelope (outer message) against schema v0. */
export function validateEnvelope(event: unknown): ValidationResult {
  return toResult(envelopeValidator(event), envelopeValidator.errors);
}

const eventValidator: ValidateFunction = ajv.compile(loadSchema("event"));

/** Validate a safety-event `data` payload against schema v0. */
export function validateEventData(data: unknown): ValidationResult {
  return toResult(eventValidator(data), eventValidator.errors);
}

const analyticsValidator: ValidateFunction = ajv.compile(loadSchema("analytics"));

/** Validate an `org.mobisentra.analytics.*` `data` payload against schema v0 (A1). */
export function validateAnalyticsData(data: unknown): ValidationResult {
  return toResult(analyticsValidator(data), analyticsValidator.errors);
}
