/**
 * FeedPanel unit tests (9.1b) — jsdom + RTL, no stack: fetch is stubbed
 * at the global, the ws client is a hand-rolled fake that captures the
 * event callback. The reconnect behavior against a REAL socket.io
 * server lives in FeedPanel.reconnect.test.tsx.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FeedPanel from "./FeedPanel";
import type { EventRecord, IncidentSummary, Severity } from "../domain";
import type { DashboardWsClient } from "../ws/client";

const VEHICLE = "BUS-01";

function incident(id: string, severity: string, eventType = "fall_detected"): IncidentSummary {
  return {
    eventId: id,
    eventType,
    severity,
    cameraId: "cam-front",
    vehicleId: VEHICLE,
    occurredAt: "2026-08-31T08:00:00Z",
    ackedAt: null,
  };
}

function eventRecord(id: string, vehicleId = VEHICLE, severity: Severity = "HIGH"): EventRecord {
  return {
    id,
    source: `/mobisentra/edge/${vehicleId}/cam-front`,
    vehicleId,
    cameraId: "cam-front",
    eventType: "fall_detected",
    severity,
    occurredAt: "2026-08-31T08:00:01Z",
    tracks: [3],
    location: null,
    evidenceRef: null,
    modelVersions: {},
    occupancy: null,
    raw: {},
  };
}

/** Captures the registered event callback; `satisfies` keeps it honest. */
function fakeWs(): DashboardWsClient & { push: (record: EventRecord) => void } {
  let onEventCallback: ((record: EventRecord) => void) | null = null;
  const ws = {
    connect: () => {},
    close: () => {},
    subscribeVehicle: async () => true,
    onEvent: (callback: (record: EventRecord) => void) => {
      onEventCallback = callback;
      return () => {
        onEventCallback = null;
      };
    },
    onState: () => () => {},
    onConnectionChange: () => () => {},
  } satisfies DashboardWsClient;
  return {
    ...ws,
    push: (record: EventRecord) => {
      if (onEventCallback === null) {
        throw new Error("no event listener registered");
      }
      onEventCallback(record);
    },
  };
}

function okFetch(body: unknown): typeof fetch {
  return vi.fn(async () => ({ ok: true, json: async () => body })) as unknown as typeof fetch;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FeedPanel", () => {
  it("renders nothing and fetches nothing until a vehicle is selected", () => {
    fetchMock.mockImplementation(okFetch([]));
    const screen = render(<FeedPanel vehicleId="" ws={fakeWs()} />);
    expect(screen.getByText(/select a vehicle/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders history rows in server order with severity styling", async () => {
    fetchMock.mockImplementation(
      okFetch([incident("evt-2", "CRITICAL"), incident("evt-1", "LOW", "zone_intrusion")])
    );
    const screen = render(<FeedPanel vehicleId={VEHICLE} ws={fakeWs()} />);

    const rows = await screen.findAllByTestId("feed-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-event-id", "evt-2");
    expect(rows[0]).toHaveClass("border-l-red-500");
    expect(rows[0]?.textContent).toContain("CRITICAL");
    expect(rows[1]).toHaveAttribute("data-event-id", "evt-1");
    expect(rows[1]).toHaveClass("border-l-sky-500");

    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(String(url)).toContain("/api/incidents?");
    expect(String(url)).toContain(`vehicle=${VEHICLE}`);
    expect(String(url)).toContain("limit=50");
  });

  it("prepends live events and dedupes against loaded history", async () => {
    fetchMock.mockImplementation(okFetch([incident("evt-1", "MEDIUM")]));
    const ws = fakeWs();
    const screen = render(<FeedPanel vehicleId={VEHICLE} ws={ws} />);
    await screen.findByTestId("feed-row");

    // duplicate of a history row: no double render
    ws.push(eventRecord("evt-1"));
    await waitFor(() => expect(screen.getAllByTestId("feed-row")).toHaveLength(1));

    // fresh event: prepended on top
    ws.push(eventRecord("evt-live-1"));
    await waitFor(() => expect(screen.getAllByTestId("feed-row")).toHaveLength(2));
    const rows = screen.getAllByTestId("feed-row");
    expect(rows[0]).toHaveAttribute("data-event-id", "evt-live-1");
    expect(rows[0]).toHaveClass("border-l-orange-500"); // HIGH
    expect(screen.getByTestId("feed-count").textContent).toBe("2");
  });

  it("ignores pushes for a different vehicle (rooms are never left server-side)", async () => {
    fetchMock.mockImplementation(okFetch([incident("evt-1", "LOW")]));
    const ws = fakeWs();
    const screen = render(<FeedPanel vehicleId={VEHICLE} ws={ws} />);
    await screen.findByTestId("feed-row");

    ws.push(eventRecord("evt-foreign", "BUS-OTHER"));
    await waitFor(() => expect(screen.getByTestId("feed-count").textContent).toBe("1"));
  });

  it("refetches history and drops stale rows on vehicle switch", async () => {
    fetchMock.mockImplementation(okFetch([incident("evt-a1", "LOW")]));
    const ws = fakeWs();
    const screen = render(<FeedPanel vehicleId="BUS-A" ws={ws} />);
    await screen.findByTestId("feed-row");

    fetchMock.mockImplementation(okFetch([incident("evt-b1", "HIGH")]));
    screen.rerender(<FeedPanel vehicleId="BUS-B" ws={ws} />);

    await waitFor(() => expect(screen.getAllByTestId("feed-row")).toHaveLength(1));
    const rows = screen.getAllByTestId("feed-row");
    expect(rows[0]).toHaveAttribute("data-event-id", "evt-b1");
    const [url] = fetchMock.mock.calls.at(-1) as unknown as [string];
    expect(String(url)).toContain("vehicle=BUS-B");
  });

  it("degrades on history failure but live events still flow", async () => {
    fetchMock.mockImplementation(async () => ({ ok: false, status: 500, json: async () => ({}) }));
    const ws = fakeWs();
    const screen = render(<FeedPanel vehicleId={VEHICLE} ws={ws} />);

    await screen.findByTestId("feed-degraded");
    expect(screen.getByTestId("feed-degraded").textContent).toContain("history unavailable");

    ws.push(eventRecord("evt-live-1"));
    const row = await screen.findByTestId("feed-row");
    expect(row).toHaveAttribute("data-event-id", "evt-live-1");
  });
});
