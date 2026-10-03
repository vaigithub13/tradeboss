import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HealthResponse } from "../api/client";
import { useBackendStore } from "./backendStore";

const HEALTH: HealthResponse = {
  status: "ok",
  service: "chart-analyser-backend",
  version: "0.1.0",
  time_ist: "2026-10-03T02:46:26+05:30",
  live_trading: false,
};

describe("backendStore.poll", () => {
  beforeEach(() => {
    useBackendStore.setState({ status: "checking", health: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is connected when /api/health returns ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => HEALTH });
    vi.stubGlobal("fetch", fetchMock);

    await useBackendStore.getState().poll();

    expect(fetchMock).toHaveBeenCalledWith("/api/health", undefined);
    expect(useBackendStore.getState().status).toBe("connected");
    expect(useBackendStore.getState().health).toEqual(HEALTH);
  });

  it("is disconnected on HTTP error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 502 }));

    await useBackendStore.getState().poll();

    expect(useBackendStore.getState().status).toBe("disconnected");
    expect(useBackendStore.getState().health).toBeNull();
  });

  it("is disconnected when the network request fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await useBackendStore.getState().poll();

    expect(useBackendStore.getState().status).toBe("disconnected");
  });

  it("ignores aborted requests (state unchanged)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")),
    );

    await useBackendStore.getState().poll();

    expect(useBackendStore.getState().status).toBe("checking");
  });
});
