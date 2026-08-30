import { describe, expect, it } from "vitest";
import { loadExample } from "../src/schema/events.js";
import { toRecord } from "../src/lib/events.js";

interface EnvelopeInput {
  id?: string;
  source?: string;
  eventType?: string;
  severity?: string;
  cameraId?: string;
  timestamp?: string;
}

function envelope(input: EnvelopeInput = {}): Record<string, unknown> {
  return {
    specversion: "1.0",
    id: input.id ?? "00000000-0000-4000-8000-000000000001",
    source: input.source ?? "/mobisentra/edge/BUS_102/BUS_102_CAM_04",
    type: `org.mobisentra.event.${input.eventType ?? "fall_detected"}`,
    time: "2026-08-29T00:00:00Z",
    datacontenttype: "application/json",
    data: {
      event_type: input.eventType ?? "fall_detected",
      severity: input.severity ?? "HIGH",
      camera_id: input.cameraId ?? "BUS_102_CAM_04",
      timestamp: input.timestamp ?? "2026-08-29T00:00:00Z",
      confidence: 0.9,
      tracks: [7],
      location: "cabin",
      evidence_ref: "local://evidence/x.mp4",
      model_versions: { detector: "y@1" },
    },
  };
}

describe("toRecord", () => {
  it("maps the shared schema example end to end", () => {
    const result = toRecord(loadExample("fall_envelope"));
    expect(result.ok).toBe(true);
    if (!result.ok) {
      return;
    }
    const record = result.record;
    expect(record.id).toBe("0d7ee6e1-7f42-4a6b-9c3d-2b1a5f8e9d01");
    expect(record.vehicleId).toBe("BUS_102");
    expect(record.cameraId).toBe("BUS_102_CAM_04");
    expect(record.eventType).toBe("fall_detected");
    expect(record.severity).toBe("HIGH");
    expect(record.occurredAt).toBe("2026-08-25T13:20:42Z");
    expect(record.tracks).toEqual([27]);
    expect(record.location).toBe("coach_rear");
    expect(record.evidenceRef).toBe("local://evidence/BUS_102_CAM_04/0d7ee6e1.mp4");
    expect(record.modelVersions).toEqual({ detector: "yolo11n@v0.1.0", pose: "yolo11n-pose@v0.1.0" });
    expect(record.raw).toEqual(loadExample("fall_envelope"));
  });

  it.each([
    "fall_detected",
    "altercation_suspected",
    "overcrowding",
    "restricted_zone_entry",
    "door_obstruction",
    "occupancy_level_change",
  ])("maps a valid %s envelope", (eventType) => {
    const result = toRecord(envelope({ eventType, severity: "LOW" }));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record.eventType).toBe(eventType);
      expect(result.record.severity).toBe("LOW");
    }
  });

  it("rejects a malformed envelope with structured errors", () => {
    const missingId = envelope();
    delete (missingId as Record<string, unknown>)["id"];
    const result = toRecord(missingId);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.length).toBeGreaterThan(0);
    }
  });

  it("rejects a non-object and bad severity", () => {
    expect(toRecord(null).ok).toBe(false);
    const badSeverity = envelope({ severity: "EXTREME" });
    const result = toRecord(badSeverity);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.join(" ")).toMatch(/EXTREME|severity/);
    }
  });

  it("rejects when data.timestamp is missing", () => {
    const broken = envelope();
    delete (broken["data"] as Record<string, unknown>)["timestamp"];
    expect(toRecord(broken).ok).toBe(false);
  });

  it("source fallbacks: missing camera segment → camera from data", () => {
    const result = toRecord(envelope({ source: "/mobisentra/edge/BUS_9" }));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record.vehicleId).toBe("BUS_9");
      expect(result.record.cameraId).toBe("BUS_102_CAM_04");
    }
  });

  it("source fallbacks: non-edge shape → vehicle unknown, camera from data", () => {
    const result = toRecord(envelope({ source: "/mobisentra/other/a/b" }));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record.vehicleId).toBe("unknown");
      expect(result.record.cameraId).toBe("BUS_102_CAM_04");
    }
  });

  it("source fallbacks: nothing parseable → unknown/unknown, never a crash", () => {
    const result = toRecord(envelope({ source: "/mobisentra/" }));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record.vehicleId).toBe("unknown");
      expect(result.record.cameraId).toBe("BUS_102_CAM_04");
    }
  });

  it("optional fields degrade to nulls/empties, not errors", () => {
    const bare = envelope();
    const data = bare["data"] as Record<string, unknown>;
    delete data["tracks"];
    delete data["location"];
    delete data["evidence_ref"];
    delete data["model_versions"];
    const result = toRecord(bare);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record.tracks).toEqual([]);
      expect(result.record.location).toBeNull();
      expect(result.record.evidenceRef).toBeNull();
      expect(result.record.modelVersions).toEqual({});
    }
  });
});
