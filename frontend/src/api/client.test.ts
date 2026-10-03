import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, candlesUrl, fetchCandles, fetchSymbols, type CandlesResponse } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("candlesUrl", () => {
  it("includes only symbol and timeframe by default", () => {
    expect(candlesUrl({ symbol: "NIFTY50", timeframe: "15m" })).toBe(
      "/api/candles?symbol=NIFTY50&timeframe=15m",
    );
  });

  it("adds from / to / sessions only when given", () => {
    const url = candlesUrl({
      symbol: "NIFTY50",
      timeframe: "1D",
      from: 100,
      to: 200,
      sessions: ["normal", "muhurat"],
    });
    const p = new URL(url, "http://x").searchParams;
    expect(p.get("from")).toBe("100");
    expect(p.get("to")).toBe("200");
    expect(p.get("sessions")).toBe("normal,muhurat");
  });

  it("adds limit and before for lazy loading", () => {
    const p = new URL(
      candlesUrl({ symbol: "NIFTY50", timeframe: "15m", limit: 2000, before: 1700000000 }),
      "http://x",
    ).searchParams;
    expect(p.get("limit")).toBe("2000");
    expect(p.get("before")).toBe("1700000000");
    const none = new URL(candlesUrl({ symbol: "NIFTY50", timeframe: "15m" }), "http://x").searchParams;
    expect(none.has("limit")).toBe(false);
    expect(none.has("before")).toBe(false);
  });

  it("encodes the symbol", () => {
    expect(candlesUrl({ symbol: "NSE_INDEX|Nifty 50", timeframe: "5m" })).toContain(
      "symbol=NSE_INDEX%7CNifty+50",
    );
  });
});

describe("fetchCandles", () => {
  const body: CandlesResponse = {
    symbol: "NIFTY50",
    timeframe: "1h",
    source_minutes: 5,
    sessions: ["normal", "weekend_full"],
    has_more: false,
    has_more_newer: false,
    candles: [
      { time: 1704080700, open: 1, high: 2, low: 0.5, close: 1.5, volume: 0, oi: null },
    ],
  };

  it("returns the parsed response", async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => body });
    vi.stubGlobal("fetch", f);
    await expect(fetchCandles({ symbol: "NIFTY50", timeframe: "1h" })).resolves.toEqual(body);
    expect(f).toHaveBeenCalledWith("/api/candles?symbol=NIFTY50&timeframe=1h", undefined);
  });

  it("passes the abort signal through", async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => body });
    vi.stubGlobal("fetch", f);
    const ctl = new AbortController();
    await fetchCandles({ symbol: "NIFTY50", timeframe: "1h" }, ctl.signal);
    expect(f).toHaveBeenCalledWith(expect.any(String), { signal: ctl.signal });
  });

  it("throws ApiError carrying the server's detail message (422)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: "3m is not available: stored data is 5m" }),
      }),
    );
    const err = await fetchCandles({ symbol: "NIFTY50", timeframe: "3m" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).message).toBe("3m is not available: stored data is 5m");
  });

  it("shows a plain-text server error instead of only the status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        text: async () => "OpenAI request failed: Tunnel connection failed: 403 Forbidden",
      }),
    );
    const err = await fetchCandles({ symbol: "NIFTY50", timeframe: "5m" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe(
      "OpenAI request failed: Tunnel connection failed: 403 Forbidden",
    );
  });

  it("falls back to a generic message when the error body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error("not json");
        },
      }),
    );
    await expect(fetchCandles({ symbol: "X", timeframe: "5m" })).rejects.toThrow("HTTP 502");
  });
});

describe("fetchSymbols", () => {
  it("GETs /api/symbols", async () => {
    const payload = {
      default_sessions: ["normal", "weekend_full"],
      session_types: ["normal", "weekend_full", "special_short", "muhurat"],
      timeframes: [],
      symbols: [],
    };
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
    vi.stubGlobal("fetch", f);
    await expect(fetchSymbols()).resolves.toEqual(payload);
    expect(f).toHaveBeenCalledWith("/api/symbols", undefined);
  });
});
