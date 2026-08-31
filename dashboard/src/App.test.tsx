import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CameraView } from "./domain";
import type { ConnectionState, DashboardWsClient } from "./ws/client";

vi.mock("./api/client", () => ({
  listCameras: vi.fn(),
}));

vi.mock("./ws/client", () => ({
  createDashboardWs: vi.fn(),
}));

import { listCameras } from "./api/client";
import { createDashboardWs } from "./ws/client";
import App from "./App";

const mockedListCameras = vi.mocked(listCameras);
const mockedCreateDashboardWs = vi.mocked(createDashboardWs);

function buildMockWs(): DashboardWsClient & {
  pushConnection: (state: ConnectionState) => void;
} {
  let connectionCb: ((state: ConnectionState) => void) | null = null;
  const mock = {
    connect: vi.fn(),
    close: vi.fn(),
    subscribeVehicle: vi.fn().mockResolvedValue(true),
    onEvent: vi.fn().mockReturnValue(() => {}),
    onState: vi.fn().mockReturnValue(() => {}),
    onConnectionChange: vi.fn((cb: (state: ConnectionState) => void) => {
      connectionCb = cb;
      return () => {
        connectionCb = null;
      };
    }),
    pushConnection: (state: ConnectionState) => {
      connectionCb?.(state);
    },
  };
  return mock;
}

describe("app shell", () => {
  let ws: ReturnType<typeof buildMockWs>;

  beforeEach(() => {
    ws = buildMockWs();
    mockedCreateDashboardWs.mockReturnValue(ws);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders header, connection status, vehicle selector and all four regions", async () => {
    const cameras: CameraView[] = [
      { cameraId: "cam-front", vehicleId: "BUS-02", online: true },
      { cameraId: "cam-rear", vehicleId: "BUS-02", online: true },
      { cameraId: "cam-door", vehicleId: "BUS-07", online: false },
    ];
    mockedListCameras.mockResolvedValue(cameras);

    render(<App />);

    expect(screen.getByTestId("conn-status")).toHaveTextContent("CONNECTING");
    expect(screen.getByTestId("region-feed")).toBeInTheDocument();
    expect(screen.getByTestId("region-cameras")).toBeInTheDocument();
    expect(screen.getByTestId("region-incidents")).toBeInTheDocument();
    expect(screen.getByTestId("region-history")).toBeInTheDocument();

    const select = screen.getByTestId("vehicle-select") as HTMLSelectElement;
    await waitFor(() => {
      expect(screen.getByRole("option", { name: "BUS-02" })).toBeInTheDocument();
    });
    expect(select.options).toHaveLength(2);
    expect(select.options.item(0)?.value).toBe("BUS-02");
    expect(select.options.item(1)?.value).toBe("BUS-07");

    ws.pushConnection("connected");
    await waitFor(() => {
      expect(screen.getByTestId("conn-status")).toHaveTextContent("CONNECTED");
    });
    ws.pushConnection("offline");
    await waitFor(() => {
      expect(screen.getByTestId("conn-status")).toHaveTextContent("OFFLINE");
    });

    expect(ws.connect).toHaveBeenCalled();
  });

  it("degrades to an empty disabled selector when the registry is unreachable", async () => {
    mockedListCameras.mockRejectedValue(new Error("backend down"));

    render(<App />);

    const select = screen.getByTestId("vehicle-select") as HTMLSelectElement;
    await waitFor(() => {
      expect(screen.getByText("registry offline")).toBeInTheDocument();
    });
    expect(select).toBeDisabled();
    expect(screen.getByTestId("region-feed")).toBeInTheDocument();
  });
});
