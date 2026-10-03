import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Candle, CandlesResponse, SymbolInfo, SymbolsResponse } from "../api/client";
import {
  MAX_WINDOW_BARS,
  appendWindow,
  clearChartCache,
  prependWindow,
  timeframeState,
  useChartStore,
} from "./chartStore";

const candle = (time: number): Candle => ({ time, open: 1, high: 2, low: 0, close: 1, volume: 0, oi: null });
const run = (t0: number, n: number): Candle[] => Array.from({ length: n }, (_, i) => candle(t0 + i));
const times = (cs: Candle[]): number[] => cs.map((c) => c.time);

const NIFTY: SymbolInfo = {
  symbol: "NIFTY50",
  display_name: "NIFTY",
  instrument_key: "NSE_INDEX|Nifty 50",
  kind: "index",
  base_timeframe: "1m",
  available_timeframes: ["1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W"],
  first_time: 1,
  last_time: 2,
};
const RELIANCE: SymbolInfo = {
  ...NIFTY,
  symbol: "NSE_EQ_INE002A01018",
  display_name: "RELIANCE",
  instrument_key: "NSE_EQ|INE002A01018",
  kind: "equity",
  base_timeframe: "5m",
  available_timeframes: ["5m", "15m", "30m", "1h", "1D", "1W"],
};

let symbolList: SymbolInfo[] = [NIFTY, RELIANCE];
const symbolsBody = (): SymbolsResponse => ({
  default_sessions: ["normal", "weekend_full"],
  session_types: ["normal", "weekend_full", "special_short", "muhurat"],
  timeframes: ["1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W"],
  symbols: symbolList,
});

const body = (
  url: URL,
  candles: Candle[],
  flags: { hasMore?: boolean; hasMoreNewer?: boolean } = {},
): CandlesResponse => ({
  symbol: url.searchParams.get("symbol") ?? "",
  timeframe: url.searchParams.get("timeframe") as CandlesResponse["timeframe"],
  source_minutes: 1,
  sessions: ["normal", "weekend_full"],
  has_more: flags.hasMore ?? false,
  has_more_newer: flags.hasMoreNewer ?? false,
  candles,
});

function stub(handler: (url: URL) => CandlesResponse) {
  const f = vi.fn(async (input: string) => {
    const url = new URL(input, "http://x");
    if (url.pathname === "/api/symbols") return { ok: true, json: async () => symbolsBody() };
    if (url.pathname === "/api/candles") return { ok: true, json: async () => handler(url) };
    throw new Error(`unexpected ${input}`);
  });
  vi.stubGlobal("fetch", f);
  return f;
}
const candleCalls = (f: ReturnType<typeof stub>): URL[] =>
  f.mock.calls.map((c) => new URL(String(c[0]), "http://x")).filter((u) => u.pathname === "/api/candles");

beforeEach(() => {
  symbolList = [NIFTY, RELIANCE];
  clearChartCache();
  useChartStore.setState({
    symbols: [],
    symbol: null,
    timeframe: "15m",
    sessions: ["normal", "weekend_full"],
    candles: [],
    hasMoreOlder: false,
    hasMoreNewer: false,
    loadingOlder: false,
    loadingNewer: false,
    loaded: null,
    status: "idle",
    error: null,
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("window arithmetic (pure)", () => {
  it("prependWindow keeps everything while under the limit", () => {
    const r = prependWindow(run(0, 3), run(3, 3), 10);
    expect(times(r.candles)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(r.droppedNewer).toBe(false);
  });

  it("prependWindow drops the NEWEST (far) bars when over the limit", () => {
    const r = prependWindow(run(0, 6), run(6, 6), 10);
    expect(times(r.candles)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(r.droppedNewer).toBe(true);
  });

  it("appendWindow drops the OLDEST (far) bars when over the limit", () => {
    const r = appendWindow(run(0, 6), run(6, 6), 10);
    expect(times(r.candles)).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    expect(r.droppedOlder).toBe(true);
    expect(appendWindow(run(0, 2), run(2, 2), 10).droppedOlder).toBe(false);
  });

  it("the window is 60k bars", () => {
    expect(MAX_WINDOW_BARS).toBe(60_000);
  });
});

describe("a bounded window while paging through a long history", () => {
  /** 55k bars loaded; an older 20k chunk arrives => 75k > 60k */
  async function openWith55k() {
    const f = stub((u) => {
      if (u.searchParams.has("before")) return body(u, run(-20_000, 20_000), { hasMore: true });
      return body(u, run(0, 55_000), { hasMore: true });
    });
    await useChartStore.getState().init();
    return f;
  }

  it("loadOlder never lets the window grow past MAX_WINDOW_BARS and remembers newer bars were dropped", async () => {
    await openWith55k();
    await useChartStore.getState().loadOlder();
    const s = useChartStore.getState();
    expect(s.candles).toHaveLength(MAX_WINDOW_BARS);
    expect(s.candles[0]?.time).toBe(-20_000); // the older chunk is there
    expect(s.candles[s.candles.length - 1]?.time).toBe(39_999); // the newest 15k are gone
    expect(s.hasMoreNewer).toBe(true);
    expect(s.hasMoreOlder).toBe(true);
  });

  it("loadNewer fetches AFTER the last loaded bar, appends it and drops the far-away oldest bars", async () => {
    const f = await openWith55k();
    await useChartStore.getState().loadOlder();
    f.mockClear();
    f.mockImplementation(async (input: string) => {
      const u = new URL(input, "http://x");
      return { ok: true, json: async () => body(u, run(40_000, 15_000), { hasMoreNewer: false }) };
    });
    await useChartStore.getState().loadNewer();

    const [call] = candleCalls(f);
    expect(call?.searchParams.get("after")).toBe("39999");
    expect(call?.searchParams.has("before")).toBe(false);
    const s = useChartStore.getState();
    expect(s.candles).toHaveLength(MAX_WINDOW_BARS);
    expect(s.candles[s.candles.length - 1]?.time).toBe(54_999);
    expect(s.candles[0]?.time).toBe(-5_000); // the oldest 15k were dropped
    expect(s.hasMoreNewer).toBe(false);
    expect(s.hasMoreOlder).toBe(true); // they can be fetched again
    expect(new Set(times(s.candles)).size).toBe(s.candles.length); // no duplicates
  });

  it("loadNewer does nothing when nothing newer was dropped", async () => {
    const f = stub((u) => body(u, run(0, 10)));
    await useChartStore.getState().init();
    f.mockClear();
    await useChartStore.getState().loadNewer();
    expect(f).not.toHaveBeenCalled();
  });

  it("older / newer loads never run at the same time", async () => {
    const f = await openWith55k();
    await useChartStore.getState().loadOlder();
    f.mockClear();
    let release: (() => void) | undefined;
    f.mockImplementation(async (input: string) => {
      await new Promise<void>((r) => (release = r));
      const u = new URL(input, "http://x");
      return { ok: true, json: async () => body(u, run(40_000, 10), { hasMoreNewer: true }) };
    });
    const first = useChartStore.getState().loadNewer();
    await useChartStore.getState().loadNewer(); // ignored: already loading
    await useChartStore.getState().loadOlder(); // ignored: a newer load is in flight
    expect(candleCalls(f)).toHaveLength(1);
    release?.();
    await first;
  });

  it("the window state survives a timeframe round-trip (cached with its flags)", async () => {
    await openWith55k();
    await useChartStore.getState().loadOlder();
    const before = useChartStore.getState().candles;
    await useChartStore.getState().setTimeframe("1h");
    await useChartStore.getState().setTimeframe("15m");
    const s = useChartStore.getState();
    expect(s.candles).toBe(before);
    expect(s.hasMoreNewer).toBe(true);
  });

  it("a fresh load resets the newer flag", async () => {
    await openWith55k();
    await useChartStore.getState().loadOlder();
    expect(useChartStore.getState().hasMoreNewer).toBe(true);
    await useChartStore.getState().setTimeframe("1h"); // new scope: newest bars again
    expect(useChartStore.getState().hasMoreNewer).toBe(false);
  });
});

describe("symbol switching", () => {
  it("setSymbol loads the other symbol, keeping the timeframe when it has it", async () => {
    const f = stub((u) => body(u, run(1, 3)));
    await useChartStore.getState().init(); // NIFTY50 preferred
    expect(useChartStore.getState().symbol).toBe("NIFTY50");
    f.mockClear();
    await useChartStore.getState().setSymbol("NSE_EQ_INE002A01018");
    const s = useChartStore.getState();
    expect(s.symbol).toBe("NSE_EQ_INE002A01018");
    expect(s.timeframe).toBe("15m");
    expect(s.loaded?.symbol).toBe("NSE_EQ_INE002A01018");
    expect(candleCalls(f).map((u) => u.searchParams.get("symbol"))).toEqual(["NSE_EQ_INE002A01018"]);
  });

  it("falls back to a timeframe the new symbol supports (1m -> 15m)", async () => {
    stub((u) => body(u, run(1, 3)));
    await useChartStore.getState().init();
    await useChartStore.getState().setTimeframe("1m");
    expect(useChartStore.getState().timeframe).toBe("1m");
    await useChartStore.getState().setSymbol("NSE_EQ_INE002A01018"); // no 1m data for it
    expect(useChartStore.getState().timeframe).toBe("15m");
  });

  it("ignores an unknown or the current symbol", async () => {
    const f = stub((u) => body(u, run(1, 3)));
    await useChartStore.getState().init();
    f.mockClear();
    await useChartStore.getState().setSymbol("NIFTY50");
    await useChartStore.getState().setSymbol("NOPE");
    expect(f).not.toHaveBeenCalled();
  });

  it("each symbol has its own cache scope", async () => {
    const f = stub((u) => body(u, run(u.searchParams.get("symbol") === "NIFTY50" ? 1 : 100, 3)));
    await useChartStore.getState().init();
    await useChartStore.getState().setSymbol("NSE_EQ_INE002A01018");
    f.mockClear();
    await useChartStore.getState().setSymbol("NIFTY50"); // cached
    expect(f).not.toHaveBeenCalled();
    expect(useChartStore.getState().candles[0]?.time).toBe(1);
  });

  it("refreshSymbols(symbol) re-reads the list, drops that symbol's cache and reloads it if shown", async () => {
    const f = stub((u) => body(u, run(1, 3)));
    await useChartStore.getState().init();
    symbolList = [{ ...NIFTY, last_time: 99 }, RELIANCE];
    f.mockClear();
    await useChartStore.getState().refreshSymbols("NIFTY50");
    expect(useChartStore.getState().symbols[0]?.last_time).toBe(99);
    expect(candleCalls(f)).toHaveLength(1); // reloaded, not served from the stale cache
  });

  it("refreshSymbols for a symbol that is not shown only invalidates its cache", async () => {
    const f = stub((u) => body(u, run(1, 3)));
    await useChartStore.getState().init();
    f.mockClear();
    await useChartStore.getState().refreshSymbols("NSE_EQ_INE002A01018");
    expect(candleCalls(f)).toHaveLength(0);
  });
});

describe("1m / 3m buttons follow the stored base timeframe", () => {
  it("are enabled when 1m data exists", () => {
    expect(timeframeState(NIFTY, "1m")).toEqual({ enabled: true, reason: null });
    expect(timeframeState(NIFTY, "3m").enabled).toBe(true);
  });

  it("stay disabled, with a pointer to Sync, when only coarser data exists", () => {
    for (const tf of ["1m", "3m"] as const) {
      const s = timeframeState(RELIANCE, tf);
      expect(s.enabled).toBe(false);
      expect(s.reason).toContain("RELIANCE");
      expect(s.reason).toContain("Sync");
    }
  });
});
