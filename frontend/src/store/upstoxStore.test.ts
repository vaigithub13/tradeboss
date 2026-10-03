import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Candle, SymbolInfo, SyncJob, TokenStatus } from "../api/client";
import { clearChartCache, useChartStore } from "./chartStore";
import { JOB_POLL_MS, LOAD_MORE_DAYS, daysBefore, useUpstoxStore } from "./upstoxStore";

const candle = (t: number): Candle => ({ time: t, open: 1, high: 2, low: 0, close: 1, volume: 0, oi: null });
const info = (symbol: string, base = "1m"): SymbolInfo => ({
  symbol,
  display_name: symbol,
  instrument_key: null,
  kind: null,
  base_timeframe: base,
  available_timeframes: ["1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W"],
  first_time: 1,
  last_time: 2,
});
const job = (over: Partial<SyncJob> = {}): SyncJob => ({
  id: "j1",
  instrument_key: "NSE_EQ|X",
  symbol: "NSE_EQ_X",
  status: "running",
  windows_total: 3,
  windows_done: 0,
  bars_added: 0,
  message: "starting",
  error: null,
  auth_error: false,
  started_at: "",
  finished_at: null,
  ...over,
});
const token = (state: TokenStatus["state"]): TokenStatus => ({
  state,
  message: "m",
  expires_at: null,
  days_left: null,
  expires_soon: false,
  market_status: null,
  checked_at: null,
});

let symbols: SymbolInfo[];
let responses: Record<string, (url: URL, init?: RequestInit) => { status?: number; body: unknown }>;
const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
  const url = new URL(input, "http://x");
  const h = responses[url.pathname];
  if (!h) throw new Error(`unexpected ${input}`);
  const r = h(url, init);
  const status = r.status ?? 200;
  return { ok: status < 400, status, json: async () => r.body };
});

beforeEach(() => {
  vi.useFakeTimers();
  symbols = [info("NIFTY50")];
  responses = {
    "/api/symbols": () => ({
      body: { default_sessions: ["normal", "weekend_full"], session_types: [], timeframes: [], symbols },
    }),
    "/api/candles": (u) => ({
      body: {
        symbol: u.searchParams.get("symbol"),
        timeframe: u.searchParams.get("timeframe"),
        source_minutes: 1,
        sessions: ["normal", "weekend_full"],
        has_more: false,
        has_more_newer: false,
        candles: [candle(5)],
      },
    }),
  };
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  clearChartCache();
  useChartStore.setState({ symbols, symbol: "NIFTY50", timeframe: "15m", loaded: null, candles: [], status: "idle", error: null });
  useUpstoxStore.setState({ token: null, tokenCheckFailed: false, jobs: {} });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const calls = (path: string): URL[] =>
  fetchMock.mock.calls.map((c) => new URL(String(c[0]), "http://x")).filter((u) => u.pathname === path);

describe("data token status", () => {
  it("stores whatever the backend reports", async () => {
    responses["/api/upstox/status"] = () => ({ body: token("expired") });
    await useUpstoxStore.getState().pollToken();
    expect(useUpstoxStore.getState().token?.state).toBe("expired");
    expect(useUpstoxStore.getState().tokenCheckFailed).toBe(false);
  });

  it("refresh=true is sent when asked (bypasses the server cache)", async () => {
    responses["/api/upstox/status"] = () => ({ body: token("valid") });
    await useUpstoxStore.getState().pollToken(true);
    expect(calls("/api/upstox/status")[0]?.searchParams.get("refresh")).toBe("true");
  });

  it("a failing request is flagged instead of throwing", async () => {
    responses["/api/upstox/status"] = () => ({ status: 500, body: { detail: "boom" } });
    await expect(useUpstoxStore.getState().pollToken()).resolves.toBeUndefined();
    expect(useUpstoxStore.getState().tokenCheckFailed).toBe(true);
  });
});

describe("sync", () => {
  it("follows a job to the end, publishing progress for the UI", async () => {
    let polls = 0;
    responses["/api/history/sync"] = () => ({ body: job() });
    responses["/api/history/jobs/j1"] = () => {
      polls++;
      return { body: polls < 3 ? job({ windows_done: polls }) : job({ status: "done", windows_done: 3, bars_added: 10 }) };
    };
    const p = useUpstoxStore.getState().sync("NSE_EQ|X");
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS * 3);
    const final = await p;
    expect(final?.status).toBe("done");
    expect(useUpstoxStore.getState().jobs["NSE_EQ|X"]?.bars_added).toBe(10);
    expect(polls).toBe(3);
  });

  it("sends from_date only when given", async () => {
    let body: unknown;
    responses["/api/history/sync"] = (_u, init) => {
      body = JSON.parse(String(init?.body));
      return { body: job({ status: "done" }) };
    };
    await useUpstoxStore.getState().sync("NSE_EQ|X");
    expect(body).toEqual({ instrument_key: "NSE_EQ|X" });
    await useUpstoxStore.getState().sync("NSE_EQ|X", "2026-01-01");
    expect(body).toEqual({ instrument_key: "NSE_EQ|X", from_date: "2026-01-01" });
  });

  it("a request that cannot start becomes a visible failed job, not an exception", async () => {
    responses["/api/history/sync"] = () => ({ status: 404, body: { detail: "Unknown instrument 'X'" } });
    const r = await useUpstoxStore.getState().sync("NSE_EQ|X");
    expect(r).toBeNull();
    expect(useUpstoxStore.getState().jobs["NSE_EQ|X"]).toMatchObject({ status: "error", error: "Unknown instrument 'X'" });
  });

  it("an auth failure re-checks the data token so the header badge updates", async () => {
    responses["/api/history/sync"] = () => ({ body: job({ status: "error", auth_error: true, error: "rejected" }) });
    responses["/api/upstox/status"] = () => ({ body: token("invalid") });
    await useUpstoxStore.getState().sync("NSE_EQ|X");
    await vi.advanceTimersByTimeAsync(0);
    expect(calls("/api/upstox/status")[0]?.searchParams.get("refresh")).toBe("true");
    expect(useUpstoxStore.getState().token?.state).toBe("invalid");
  });

  it("losing the connection while polling ends the job as an error (no endless loop)", async () => {
    responses["/api/history/sync"] = () => ({ body: job() });
    responses["/api/history/jobs/j1"] = () => ({ status: 500, body: {} });
    const p = useUpstoxStore.getState().sync("NSE_EQ|X");
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS);
    expect((await p)?.status).toBe("error");
  });
});

describe("openInstrument", () => {
  it("a stored instrument just becomes the current symbol (no sync)", async () => {
    symbols = [info("NIFTY50"), info("NSE_EQ_X", "5m")];
    await useUpstoxStore.getState().openInstrument({ instrument_key: "NSE_EQ|X", symbol_id: "NSE_EQ_X", has_data: true });
    expect(useChartStore.getState().symbol).toBe("NSE_EQ_X");
    expect(calls("/api/history/sync")).toHaveLength(0);
  });

  it("an instrument without data is fetched first, then shown", async () => {
    responses["/api/history/sync"] = () => ({ body: job({ status: "running" }) });
    responses["/api/history/jobs/j1"] = () => {
      symbols = [info("NIFTY50"), info("NSE_EQ_X")]; // the sync created the symbol
      return { body: job({ status: "done", windows_done: 3 }) };
    };
    const p = useUpstoxStore.getState().openInstrument({ instrument_key: "NSE_EQ|X", symbol_id: "NSE_EQ_X", has_data: false });
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS);
    await p;
    expect(useChartStore.getState().symbol).toBe("NSE_EQ_X");
    expect(useChartStore.getState().loaded?.symbol).toBe("NSE_EQ_X");
  });

  it("a failed fetch leaves the current chart untouched", async () => {
    responses["/api/history/sync"] = () => ({ body: job({ status: "error", error: "no token", auth_error: true }) });
    responses["/api/upstox/status"] = () => ({ body: token("missing") });
    await useUpstoxStore.getState().openInstrument({ instrument_key: "NSE_EQ|X", symbol_id: "NSE_EQ_X", has_data: false });
    expect(useChartStore.getState().symbol).toBe("NIFTY50");
    expect(useUpstoxStore.getState().jobs["NSE_EQ|X"]?.error).toBe("no token");
  });
});

describe("load more history / sync latest", () => {
  it("daysBefore does plain date arithmetic", () => {
    expect(daysBefore("2026-09-14", 90)).toBe("2026-06-16");
    expect(daysBefore("2026-01-10", 15)).toBe("2025-12-26");
  });

  it("goes back LOAD_MORE_DAYS before the oldest stored day, never before the minimum date", async () => {
    let from: string | undefined;
    responses["/api/history/coverage"] = () => ({
      body: { instrument_key: "NSE_EQ|X", symbol_id: "NSE_EQ_X", covered: [["2026-09-01", "2026-10-02"]], min_history_date: "2022-01-01" },
    });
    responses["/api/history/sync"] = (_u, init) => {
      from = JSON.parse(String(init?.body)).from_date;
      return { body: job({ status: "done" }) };
    };
    await useUpstoxStore.getState().loadMoreHistory("NSE_EQ|X");
    expect(from).toBe(daysBefore("2026-09-01", LOAD_MORE_DAYS));

    responses["/api/history/coverage"] = () => ({
      body: { instrument_key: "NSE_EQ|X", symbol_id: "NSE_EQ_X", covered: [["2022-01-20", "2026-10-02"]], min_history_date: "2022-01-01" },
    });
    await useUpstoxStore.getState().loadMoreHistory("NSE_EQ|X");
    expect(from).toBe("2022-01-01");
  });

  it("syncLatest re-reads the symbols so the chart sees the new range", async () => {
    responses["/api/history/sync"] = () => ({ body: job({ status: "done", symbol: "NIFTY50" }) });
    symbols = [{ ...info("NIFTY50"), last_time: 777 }];
    await useUpstoxStore.getState().syncLatest("NSE_INDEX|Nifty 50");
    expect(useChartStore.getState().symbols[0]?.last_time).toBe(777);
  });
});
