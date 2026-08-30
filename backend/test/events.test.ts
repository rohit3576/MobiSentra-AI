import { describe, expect, it } from "vitest";
import { loadExample } from "../src/schema/events.js";
import { envelopeKind, toAnalyticsState, toRecord } from "../src/lib/events.js";

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

describe("occupancy extraction (A1 live path)", () => {
  function occupancyEnvelope(): Record<string, unknown> {
    const message = envelope({ eventType: "occupancy_level_change", severity: "LOW" });
    Object.assign(message["data"] as Record<string, unknown>, {
      zone: "cabin",
      from_band: "NORMAL",
      to_band: "MODERATE",
      count: 14,
      ratio: 0.74,
    });
    return message;
  }

  it("band-flip event → full OccupancyInfo (zone/level/count/ratio)", () => {
    const result = toRecord(occupancyEnvelope());
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record.occupancy).toEqual({ zone: "cabin", level: "MODERATE", peopleCount: 14, ratio: 0.74 });
    }
  });

  it("non-occupancy event → occupancy null (the shared fall example)", () => {
    const result = toRecord(loadExample("fall_envelope"));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record.occupancy).toBeNull();
    }
  });

  it("to_band is the marker — count/ratio without it extract nothing", () => {
    const message = occupancyEnvelope();
    delete (message["data"] as Record<string, unknown>)["to_band"];
    const result = toRecord(message);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record.occupancy).toBeNull();
    }
  });
});

describe("envelopeKind + toAnalyticsState (A1 contract path)", () => {
  function analyticsEnvelope(): Record<string, unknown> {
    return {
      specversion: "1.0",
      id: "20000000-0000-4000-8000-000000000002",
      source: "/mobisentra/edge/BUS_102/CAM_04",
      type: "org.mobisentra.analytics.occupancy.v0",
      time: "2026-08-30T09:00:05Z",
      datacontenttype: "application/json",
      data: {
        camera_id: "BUS_102_CAM_04",
        zone: "cabin",
        people_count: 14,
        occupancy_ratio: 0.74,
        level: "MODERATE",
        ts: "2026-08-30T09:00:05Z",
      },
    };
  }

  it("routes by type prefix; garbage degrades to safety (never a crash)", () => {
    expect(envelopeKind(analyticsEnvelope())).toBe("analytics");
    expect(envelopeKind(loadExample("fall_envelope"))).toBe("safety");
    expect(envelopeKind({})).toBe("safety");
    expect(envelopeKind("garbage")).toBe("safety");
  });

  it("maps a schema-valid analytics envelope, optional ratio included", () => {
    const result = toAnalyticsState(analyticsEnvelope());
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.state).toEqual({
        vehicleId: "BUS_102",
        cameraId: "BUS_102_CAM_04",
        zone: "cabin",
        level: "MODERATE",
        peopleCount: 14,
        occupancyRatio: 0.74,
        occurredAt: "2026-08-30T09:00:05Z",
      });
    }
  });

  it("ratio is optional — absent degrades to null, not an error", () => {
    const message = analyticsEnvelope();
    delete (message["data"] as Record<string, unknown>)["occupancy_ratio"];
    const result = toAnalyticsState(message);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.state.occupancyRatio).toBeNull();
    }
  });

  it("rejects an analytics envelope missing a required field (zone)", () => {
    const message = analyticsEnvelope();
    delete (message["data"] as Record<string, unknown>)["zone"];
    const result = toAnalyticsState(message);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.length).toBeGreaterThan(0);
    }
  });
});
