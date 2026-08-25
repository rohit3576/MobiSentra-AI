/**
 * Event schema v0 validation tests (Phase 0, Step 0.9).
 * Mirrors edge/tests/test_schemas.py — same schemas, same example, both languages.
 */
import { describe, expect, it } from "vitest";
import { createAjv, loadExample, loadSchema, validateEnvelope, validateEventData } from "./events.js";

const fallEnvelope = loadExample("fall_envelope") as Record<string, unknown>;

describe("schema v0 files", () => {
  it("all schema files are valid draft-07", () => {
    for (const schema of [
      loadSchema("envelope"),
      loadSchema("event"),
      loadSchema("detection"),
      loadSchema("alert"),
      loadSchema("analytics"),
    ]) {
      const ajv = createAjv();
      expect(() => ajv.compile(schema)).not.toThrow();
    }
  });
});

describe("fall example envelope", () => {
  it("validates against the envelope schema", () => {
    const result = validateEnvelope(fallEnvelope);
    expect(result.valid).toBe(true);
    expect(result.errors).toEqual([]);
  });

  it("data validates against the event schema", () => {
    const result = validateEventData(fallEnvelope["data"]);
    expect(result.valid).toBe(true);
  });

  it("rejects envelopes missing core attributes", () => {
    for (const attr of ["specversion", "id", "source", "type", "time", "data"]) {
      const broken = { ...fallEnvelope };
      delete broken[attr];
      expect(validateEnvelope(broken).valid).toBe(false);
    }
  });

  it("rejects bad source and type patterns", () => {
    expect(validateEnvelope({ ...fallEnvelope, source: "BUS_102" }).valid).toBe(false);
    expect(validateEnvelope({ ...fallEnvelope, type: "random.event" }).valid).toBe(false);
  });
});

describe("event payload constraints", () => {
  it("rejects bad severity and confidence", () => {
    const data = fallEnvelope["data"] as Record<string, unknown>;
    expect(validateEventData({ ...data, severity: "EXTREME" }).valid).toBe(false);
    expect(validateEventData({ ...data, confidence: 1.5 }).valid).toBe(false);
  });
});
