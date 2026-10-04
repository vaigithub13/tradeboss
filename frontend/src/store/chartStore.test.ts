import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Candle, CandlesResponse, SymbolInfo, SymbolsResponse } from "../api/client";
import { setReplayCursor } from "../replay/session";
import { INITIAL_BARS, OLDER_BARS, OLDER_BARS_MAX, clearChartCache, olderChunkSize, timeframeState, useChartStore } from "./chartStore";

const INFO: SymbolInfo = {
  symbol: "NIFTY50",
  display_name: "NIFTY",
  instrument_key: "NSE_INDEX|Nifty 50",
  kind: "index",
  base_timeframe: "5m",
  available_timeframes: ["5m", "15m", "30m", "1h", "1D", "1W"],
  first_time: 1,
  last_time: 2,
};

const SYMBOLS: SymbolsResponse = {
  default_sessions: ["normal", "weekend_full"],
  session_types: ["normal", "weekend_full", "special_short", "muhurat"],
  timeframes: ["1m", "3m", "5m", "15m", "30m", "1h", "1D", "1W"],
  symbols: [INFO],
};

const candle = (time: number, close = 1): Candle => ({
  time,
  open: 1,
  high: 2,
  low: 0,
  close,
  volume: 0,
  oi: null,
});

const candlesBody = (
  tf: string,
  candles: Candle[],
  sessions: CandlesResponse["sessions"] = ["normal", "weekend_full"],
  hasMore = false,
): CandlesResponse => ({
  symbol: "NIFTY50",
  timeframe: tf as CandlesResponse["timeframe"],
  source_minutes: 5,
  sessions,
  has_more: hasMore,
  has_more_newer: false,
  candles,
});

const ok = (body: unknown) => ({ ok: true, json: async () => body });

/** Route fetch by URL path. */
function stubApi(handlers: { candles?: (url: URL) => unknown | Promise<unknown> }) {
  const f = vi.fn(async (input: string) => {
    const url = new URL(input, "http://x");
    if (url.pathname === "/api/symbols") return ok(SYMBOLS);
    if (url.pathname === "/api/candles" && handlers.candles) {
      return ok(await handlers.candles(url));
    }
    throw new Error(`unexpected ${input}`);
  });
  vi.stubGlobal("fetch", f);
  return f;
}

const candleCalls = (f: ReturnType<typeof stubApi>): URL[] =>
  f.mock.calls
    .map((c) => new URL(String(c[0]), "http://x"))
    .filter((u) => u.pathname === "/api/candles");

beforeEach(() => {
  setReplayCursor(null);
  clearChartCache();
  useChartStore.setState({
    hasMoreOlder: false,
    hasMoreNewer: false,
    loadingOlder: false,
    loadingNewer: false,
    symbols: [],
    symbol: null,
    timeframe: "15m",
    sessions: ["normal", "weekend_full"],
    candles: [],
    loaded: null,
    status: "idle",
    error: null,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("timeframeState", () => {
  it("enables timeframes the backend can build", () => {
    expect(timeframeState(INFO, "15m")).toEqual({ enabled: true, reason: null });
    expect(timeframeState(INFO, "1W").enabled).toBe(true);
  });

  it("disables 1m and 3m with an explanatory tooltip (never fabricated)", () => {
    for (const tf of ["1m", "3m"] as const) {
      const s = timeframeState(INFO, tf);
      expect(s.enabled).toBe(false);
      expect(s.reason).toContain(`Needs ${tf} data`);
      expect(s.reason).toContain("5m");
      expect(s.reason).toContain("Sync");
    }
  });

  it("is disabled while symbols are not loaded", () => {
    expect(timeframeState(undefined, "5m").enabled).toBe(false);
  });
});

describe("init", () => {
  it("loads symbols, picks 15m + the server's session defaults, then loads candles", async () => {
    const f = stubApi({ candles: (u) => candlesBody(u.searchParams.get("timeframe")!, [candle(100)]) });
    await useChartStore.getState().init();

    const s = useChartStore.getState();
    expect(s.symbol).toBe("NIFTY50");
    expect(s.timeframe).toBe("15m");
    expect(s.sessions).toEqual(["normal", "weekend_full"]);
    expect(s.status).toBe("ready");
    expect(s.candles).toEqual([candle(100)]);
    expect(s.loaded).toEqual({
      symbol: "NIFTY50",
      timeframe: "15m",
      sessions: ["normal", "weekend_full"],
    });
    const [call] = candleCalls(f);
    expect(call?.searchParams.get("symbol")).toBe("NIFTY50");
    expect(call?.searchParams.get("sessions")).toBe("normal,weekend_full");
  });

  it("reports an error when the symbols request fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await useChartStore.getState().init();
    const s = useChartStore.getState();
    expect(s.status).toBe("error");
    expect(s.error).toContain("Could not load symbols");
  });

  it("reports an error when there is no stored data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(ok({ ...SYMBOLS, symbols: [] })),
    );
    await useChartStore.getState().init();
    expect(useChartStore.getState().status).toBe("error");
    expect(useChartStore.getState().error).toContain("sample import");
  });
});

describe("setTimeframe", () => {
  async function ready() {
    const f = stubApi({ candles: (u) => candlesBody(u.searchParams.get("timeframe")!, [candle(1)]) });
    await useChartStore.getState().init();
    f.mockClear();
    return f;
  }

  it("switches timeframe and reloads", async () => {
    const f = await ready();
    await useChartStore.getState().setTimeframe("1h");
    expect(useChartStore.getState().timeframe).toBe("1h");
    expect(useChartStore.getState().loaded?.timeframe).toBe("1h");
    expect(candleCalls(f).map((u) => u.searchParams.get("timeframe"))).toEqual(["1h"]);
  });

  it("ignores disabled timeframes (1m/3m): no state change, no request", async () => {
    const f = await ready();
    await useChartStore.getState().setTimeframe("1m");
    await useChartStore.getState().setTimeframe("3m");
    expect(useChartStore.getState().timeframe).toBe("15m");
    expect(f).not.toHaveBeenCalled();
  });

  it("does nothing when the timeframe is already selected", async () => {
    const f = await ready();
    await useChartStore.getState().setTimeframe("15m");
    expect(f).not.toHaveBeenCalled();
  });

  it("a slow older response never overwrites a newer one", async () => {
    const f = await ready();
    let releaseSlow: (() => void) | undefined;
    f.mockImplementation(async (input: string) => {
      const url = new URL(input, "http://x");
      const tf = url.searchParams.get("timeframe")!;
      if (tf === "30m") {
        await new Promise<void>((r) => {
          releaseSlow = r;
        });
        return ok(candlesBody("30m", [candle(30)]));
      }
      return ok(candlesBody(tf, [candle(60)]));
    });

    const slow = useChartStore.getState().setTimeframe("30m"); // starts, hangs
    await useChartStore.getState().setTimeframe("1h"); // completes first
    releaseSlow?.();
    await slow;

    const s = useChartStore.getState();
    expect(s.timeframe).toBe("1h");
    expect(s.candles).toEqual([candle(60)]);
    expect(s.loaded?.timeframe).toBe("1h");
  });

  it("keeps the previous candles on screen while loading, and surfaces API errors", async () => {
    const f = await ready();
    f.mockImplementation(async () => ({
      ok: false,
      status: 422,
      json: async () => ({ detail: "boom" }),
    }));
    await useChartStore.getState().setTimeframe("1h");
    const s = useChartStore.getState();
    expect(s.status).toBe("error");
    expect(s.error).toBe("boom");
    expect(s.candles).toEqual([candle(1)]);
  });
});

describe("toggleSession", () => {
  async function ready() {
    const f = stubApi({
      candles: (u) =>
        candlesBody(
          "15m",
          [candle(1)],
          u.searchParams.get("sessions")!.split(",") as CandlesResponse["sessions"],
        ),
    });
    await useChartStore.getState().init();
    f.mockClear();
    return f;
  }

  it("adds / removes a session type and reloads with the explicit list", async () => {
    const f = await ready();

    await useChartStore.getState().toggleSession("muhurat");
    expect(useChartStore.getState().sessions).toEqual(["normal", "weekend_full", "muhurat"]);
    expect(useChartStore.getState().loaded?.sessions).toEqual([
      "normal",
      "weekend_full",
      "muhurat",
    ]);
    expect(candleCalls(f)[0]?.searchParams.get("sessions")).toBe("normal,weekend_full,muhurat");

    await useChartStore.getState().toggleSession("special_short");
    expect(candleCalls(f)[1]?.searchParams.get("sessions")).toBe(
      "normal,weekend_full,special_short,muhurat",
    );

    await useChartStore.getState().toggleSession("weekend_full");
    expect(candleCalls(f)[2]?.searchParams.get("sessions")).toBe("normal,special_short,muhurat");
  });

  it("never turns 'normal' off", async () => {
    const f = await ready();
    await useChartStore.getState().toggleSession("normal");
    expect(useChartStore.getState().sessions).toContain("normal");
    expect(f).not.toHaveBeenCalled();
  });
});

describe("lazy loading", () => {
  /** n candles with start times t0, t0+1, ... */
  const run = (t0: number, n: number): Candle[] => Array.from({ length: n }, (_, i) => candle(t0 + i));

  it("opens on only the newest INITIAL_BARS candles", async () => {
    const f = stubApi({ candles: () => candlesBody("15m", run(1000, 5), undefined, true) });
    await useChartStore.getState().init();
    const [call] = candleCalls(f);
    expect(call?.searchParams.get("limit")).toBe(String(INITIAL_BARS));
    expect(call?.searchParams.has("before")).toBe(false);
    expect(useChartStore.getState().hasMoreOlder).toBe(true);
  });

  it("loadOlder during replay keeps the cursor bar and drops the session close", async () => {
    const cursor = 1_790_838_900;
    const sessionClose = 1_790_848_500;
    const f = stubApi({
      candles: (u) => {
        const asked = u.searchParams.get("cursor");
        if (u.searchParams.has("before")) return candlesBody("15m", [candle(cursor - 900, 22400)], undefined, false);
        if (asked === String(cursor)) {
          return candlesBody("15m", [candle(cursor - 900, 22433.7), candle(cursor, 22416)], undefined, true);
        }
        return candlesBody(
          "15m",
          [candle(cursor - 900, 22433.7), candle(cursor, 22324.8), candle(sessionClose, 22421.95)],
          undefined,
          true,
        );
      },
    });
    await useChartStore.getState().init();
    expect(useChartStore.getState().candles.at(-1)?.close).toBe(22421.95);

    setReplayCursor(cursor);
    await useChartStore.getState().load();
    await useChartStore.getState().loadOlder();

    const shown = useChartStore.getState().candles;
    expect(shown.some((bar) => bar.time > cursor)).toBe(false);
    expect(shown.find((bar) => bar.time === cursor)?.close).toBe(22416);
    expect(shown.some((bar) => bar.close === 22421.95 || bar.close === 22324.8)).toBe(false);
    expect(candleCalls(f).some((url) => url.searchParams.get("cursor") === String(cursor))).toBe(true);
  });

  it("loadOlder fetches the chunk before the first candle and prepends it", async () => {
    const f = stubApi({
      candles: (u) =>
        u.searchParams.has("before")
          ? candlesBody("15m", run(990, 10), undefined, true)
          : candlesBody("15m", run(1000, 5), undefined, true),
    });
    await useChartStore.getState().init();
    await useChartStore.getState().loadOlder();

    const calls = candleCalls(f);
    expect(calls).toHaveLength(2);
    expect(calls[1]?.searchParams.get("before")).toBe("1000");
    expect(calls[1]?.searchParams.get("limit")).toBe(String(OLDER_BARS));
    const s = useChartStore.getState();
    expect(s.candles.map((c) => c.time)).toEqual([...run(990, 10), ...run(1000, 5)].map((c) => c.time));
    expect(s.hasMoreOlder).toBe(true);
    expect(s.loadingOlder).toBe(false);
  });

  it("chunk size grows with what is loaded, between 4k and 20k", () => {
    expect(olderChunkSize(2000)).toBe(OLDER_BARS);
    expect(olderChunkSize(6000)).toBe(6000);
    expect(olderChunkSize(12000)).toBe(12000);
    expect(olderChunkSize(100000)).toBe(OLDER_BARS_MAX);
  });

  it("stops asking once the server says there is nothing older", async () => {
    const f = stubApi({
      candles: (u) =>
        u.searchParams.has("before")
          ? candlesBody("15m", run(990, 10), undefined, false)
          : candlesBody("15m", run(1000, 5), undefined, true),
    });
    await useChartStore.getState().init();
    await useChartStore.getState().loadOlder();
    await useChartStore.getState().loadOlder();
    await useChartStore.getState().loadOlder();
    expect(candleCalls(f)).toHaveLength(2);
    expect(useChartStore.getState().hasMoreOlder).toBe(false);
  });

  it("never runs two older-chunk requests at once", async () => {
    let release: (() => void) | undefined;
    const f = stubApi({
      candles: async (u) => {
        if (!u.searchParams.has("before")) return candlesBody("15m", run(1000, 5), undefined, true);
        await new Promise<void>((r) => (release = r));
        return candlesBody("15m", run(990, 10), undefined, true);
      },
    });
    await useChartStore.getState().init();
    const a = useChartStore.getState().loadOlder();
    const b = useChartStore.getState().loadOlder(); // while the first is in flight
    expect(useChartStore.getState().loadingOlder).toBe(true);
    release?.();
    await Promise.all([a, b]);
    expect(candleCalls(f)).toHaveLength(2);
    expect(useChartStore.getState().candles).toHaveLength(15);
  });

  it("does nothing before a chart is loaded", async () => {
    const f = stubApi({ candles: () => candlesBody("15m", []) });
    await useChartStore.getState().loadOlder();
    expect(f).not.toHaveBeenCalled();
  });

  it("an older chunk that arrives after switching timeframe is cached, not shown", async () => {
    let release: (() => void) | undefined;
    const f = stubApi({
      candles: async (u) => {
        const tf = u.searchParams.get("timeframe")!;
        if (u.searchParams.has("before")) {
          await new Promise<void>((r) => (release = r));
          return candlesBody(tf, run(990, 10), undefined, false);
        }
        return candlesBody(tf, run(tf === "15m" ? 1000 : 5000, 5), undefined, true);
      },
    });
    await useChartStore.getState().init();
    const older = useChartStore.getState().loadOlder();
    await useChartStore.getState().setTimeframe("1h");
    release?.();
    await older;

    expect(useChartStore.getState().timeframe).toBe("1h");
    expect(useChartStore.getState().candles.map((c) => c.time)).toEqual(run(5000, 5).map((c) => c.time));
    f.mockClear();
    await useChartStore.getState().setTimeframe("15m"); // back: the merged chunks come from cache
    expect(f).not.toHaveBeenCalled();
    expect(useChartStore.getState().candles).toHaveLength(15);
  });
});

describe("caching loaded chunks", () => {
  it("switching timeframe back and forth does not refetch", async () => {
    const f = stubApi({ candles: (u) => candlesBody(u.searchParams.get("timeframe")!, [candle(1)], undefined, true) });
    await useChartStore.getState().init(); // 15m
    await useChartStore.getState().setTimeframe("1h");
    await useChartStore.getState().setTimeframe("15m");
    await useChartStore.getState().setTimeframe("1h");
    await useChartStore.getState().setTimeframe("15m");
    expect(candleCalls(f).map((u) => u.searchParams.get("timeframe"))).toEqual(["15m", "1h"]);
    const s = useChartStore.getState();
    expect(s.status).toBe("ready");
    expect(s.loaded?.timeframe).toBe("15m");
    expect(s.hasMoreOlder).toBe(true); // restored with the cached chunk
  });

  it("keeps separate caches per session selection", async () => {
    const f = stubApi({
      candles: (u) =>
        candlesBody("15m", [candle(1)], u.searchParams.get("sessions")!.split(",") as CandlesResponse["sessions"]),
    });
    await useChartStore.getState().init();
    await useChartStore.getState().toggleSession("muhurat"); // new scope -> fetch
    await useChartStore.getState().toggleSession("muhurat"); // back to the first scope -> cached
    expect(candleCalls(f)).toHaveLength(2);
  });

  it("a cache hit supersedes a slow request that is still in flight", async () => {
    let release: (() => void) | undefined;
    const f = stubApi({
      candles: async (u) => {
        const tf = u.searchParams.get("timeframe")!;
        if (tf === "30m") {
          await new Promise<void>((r) => (release = r));
        }
        return candlesBody(tf, [candle(tf === "30m" ? 30 : 15)]);
      },
    });
    await useChartStore.getState().init();
    const slow = useChartStore.getState().setTimeframe("30m");
    await useChartStore.getState().setTimeframe("15m"); // cached -> shown immediately
    release?.();
    await slow;
    expect(useChartStore.getState().timeframe).toBe("15m");
    expect(useChartStore.getState().candles).toEqual([candle(15)]);
    expect(candleCalls(f).map((u) => u.searchParams.get("timeframe"))).toEqual(["15m", "30m"]);
  });
});

describe("showRange", () => {
  it("replaces the window with the candles covering a drawing that starts before the loaded bars", async () => {
    const f = stubApi({
      candles: (u) => {
        if (u.searchParams.get("from") === "10") return candlesBody("15m", [candle(10), candle(20), candle(50)]);
        return candlesBody("15m", [candle(100), candle(200)]);
      },
    });
    await useChartStore.getState().init();
    expect(useChartStore.getState().candles.map((c) => c.time)).toEqual([100, 200]);
    await useChartStore.getState().showRange(50, 10);
    const call = candleCalls(f).at(-1);
    expect(call?.searchParams.get("from")).toBe("10");
    expect(call?.searchParams.get("to")).toBe("50");
    expect(call?.searchParams.get("limit")).toBeNull();
    expect(useChartStore.getState().candles.map((c) => c.time)).toEqual([10, 20, 50]);
    expect(useChartStore.getState().hasMoreOlder).toBe(true);
  });
});
