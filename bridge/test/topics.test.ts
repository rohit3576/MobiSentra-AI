import { describe, expect, it } from "vitest";
import { mapTopic } from "../src/lib/topics.js";

describe("mapTopic", () => {
  it("maps the canonical topic", () => {
    expect(mapTopic("mobisentra/events", "mobisentra")).toBe("mobisentra.events");
  });

  it("maps every slash to a dot", () => {
    expect(mapTopic("mobisentra/a/b/c", "mobisentra")).toBe("mobisentra.a.b.c");
  });

  it("drops the Phase-0 dotted-topic gotcha", () => {
    // a dotted topic never matches mobisentra/# and has no <prefix>/ head
    expect(mapTopic("mobisentra.events", "mobisentra")).toBeNull();
  });

  it("drops foreign prefixes and the bare prefix", () => {
    expect(mapTopic("other/events", "mobisentra")).toBeNull();
    expect(mapTopic("mobisentra", "mobisentra")).toBeNull();
    expect(mapTopic("", "mobisentra")).toBeNull();
  });

  it("honors a configured prefix", () => {
    expect(mapTopic("custom/events", "custom")).toBe("custom.events");
    expect(mapTopic("mobisentra/events", "custom")).toBeNull();
  });
});
