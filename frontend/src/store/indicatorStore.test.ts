import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Candle } from "../api/client";
import type { IndicatorsRequest, IndicatorsResponse } from "../api/indicators";
import { createInstance } from "../indicators/catalog";
import { indicatorKey, scopeKey } from "../indicators/cache";
import type { RequestContext } from "../indicators/request";
import { clearIndicatorCache, useIndicatorStore } from "./indicatorStore";

const candle = (time: number): Candle => ({ time, open: 1, high: 2, low: 0, close: 1, volume: 5, oi: null });
const candles = [candle(1000), candle(1300)];
/** every candle time the fake backend knows about */
const ALL = [100, 400, 700, 1000, 1300];

const ctx = (over: Partial<RequestContext> = {}): RequestContext => ({
  symbol: "NIFTY50",
  timeframe: "15m",
  sessions: ["normal", "weekend_full"],
  candles,
  items: useIndicatorStore.getState().items,
  ...over,
});

function stubFetch(handler: (req: IndicatorsRequest) => Promise<unknown> | unknown, status = 200) {
  const f = vi.fn(async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as IndicatorsRequest;
    const out = await handler(body);
    return { ok: status === 200, status, json: async () => out };
  });
  vi.stubGlobal("fetch", f);
  return f;
}

beforeEach(() => {
  useIndicatorStore.setState({ items: [] });
  clearIndicatorCache();
});
afterEach(() => vi.unstubAllGlobals());

describe("managing indicators", () => {
  it("adds several copies of one indicator with distinct ids", () => {
    const { add } = useIndicatorStore.getState();
    const a = add("ema");
    const b = add("ema");
    expect([a, b]).toEqual(["ema-1", "ema-2"]);
    expect(useIndicatorStore.getState().items.map((i) => i.type)).toEqual(["ema", "ema"]);
  });

  it("removes only the chosen copy", () => {
    const s = useIndicatorStore.getState();
    s.add("ema");
    s.add("ema");
    s.remove("ema-1");
    expect(useIndicatorStore.getState().items.map((i) => i.id)).toEqual(["ema-2"]);
  });

  it("duplicate copies the parameters right after the original", () => {
    const s = useIndicatorStore.getState();
    s.add("ema");
    s.add("rsi");
    s.update("ema-1", { params: { length: 50, source: "hl2" } });
    const id = s.duplicate("ema-1");
    const items = useIndicatorStore.getState().items;
    expect(items.map((i) => i.id)).toEqual(["ema-1", id, "rsi-1"]);
    expect(items[1]?.params).toEqual({ length: 50, source: "hl2" });
    expect(s.duplicate("missing")).toBeNull();
  });

  it("update edits params, colours and visibility", () => {
    const s = useIndicatorStore.getState();
    s.add("bb");
    expect(s.update("bb-1", { params: { length: 30, mult: 2.5, source: "close" } })).toBeNull();
    expect(s.update("bb-1", { colors: { basis: "#abcdef" } })).toBeNull();
    expect(s.update("bb-1", { visible: false })).toBeNull();
    const bb = useIndicatorStore.getState().items[0];
    expect(bb?.params).toEqual({ length: 30, mult: 2.5, source: "close" });
    expect(bb?.colors["basis"]).toBe("#abcdef");
    expect(bb?.colors["band"]).toBeDefined(); // untouched colours are kept
    expect(bb?.visible).toBe(false);
  });

  it("rejects invalid parameters and leaves the indicator unchanged", () => {
    const s = useIndicatorStore.getState();
    s.add("macd");
    const before = useIndicatorStore.getState().items;
    expect(s.update("macd-1", { params: { fast: 40, slow: 26, signal: 9, source: "close" } })).toMatch(/smaller/);
    expect(s.update("macd-1", { params: { fast: 0, slow: 26, signal: 9, source: "close" } })).not.toBeNull();
    expect(useIndicatorStore.getState().items).toBe(before);
  });

  it("persists the item list to localStorage on every change", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    });
    useIndicatorStore.getState().add("sma");
    const saved = JSON.parse([...data.values()][0] ?? "{}") as { items: { id: string }[] };
    expect(saved.items.map((i) => i.id)).toEqual(["sma-1"]);
  });
});

describe("refresh", () => {
  /** answers each request with value = candle time, for exactly the requested range */
  const answer = (req: IndicatorsRequest): IndicatorsResponse => {
    const times = ALL.filter((t) => t >= (req.from ?? 0) && t <= (req.to ?? Infinity));
    return {
      symbol: req.symbol,
      timeframe: req.timeframe,
      sessions: [...(req.sessions ?? [])],
      times,
      indicators: req.indicators.map((i) => ({ ...i, outputs: { [i.type]: times.map((t) => t / 10) } })),
    };
  };
  const entryOf = (type: string, params: Record<string, number | string>, c: RequestContext = ctx()) =>
    useIndicatorStore.getState().data[scopeKey(c.symbol, c.timeframe, c.sessions)]?.[indicatorKey(type as "ema", params)];

  it("posts one request for the visible indicators and caches the values", async () => {
    const f = stubFetch(answer);
    useIndicatorStore.getState().add("ema");
    await useIndicatorStore.getState().refresh(ctx());

    expect(f).toHaveBeenCalledTimes(1);
    const [url, init] = f.mock.calls[0] ?? [];
    expect(url).toBe("/api/indicators");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body)) as IndicatorsRequest;
    expect(body).toMatchObject({ symbol: "NIFTY50", timeframe: "15m", from: 1000, to: 1300 });
    expect(body.sessions).toEqual(["normal", "weekend_full"]);
    expect(body.indicators).toEqual([{ id: "0", type: "ema", params: { length: 20, source: "close" } }]);

    const s = useIndicatorStore.getState();
    expect(s.status).toBe("ready");
    expect(entryOf("ema", { length: 20, source: "close" })?.times).toEqual([1000, 1300]);
  });

  it("does not refetch for colour or visibility changes", async () => {
    const f = stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("ema");
    await s.refresh(ctx());
    s.update("ema-1", { colors: { line: "#123456" } });
    await s.refresh(ctx());
    s.update("ema-1", { visible: false });
    await s.refresh(ctx());
    s.update("ema-1", { visible: true });
    await s.refresh(ctx());
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("changing one indicator's parameters refetches only that indicator", async () => {
    const f = stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("ema");
    s.add("rsi");
    await s.refresh(ctx());
    expect(f).toHaveBeenCalledTimes(1); // both in one request
    s.update("ema-1", { params: { length: 50, source: "close" } });
    await s.refresh(ctx());

    expect(f).toHaveBeenCalledTimes(2);
    const body = JSON.parse(String(f.mock.calls[1]?.[1]?.body)) as IndicatorsRequest;
    expect(body.indicators).toEqual([{ id: "0", type: "ema", params: { length: 50, source: "close" } }]);
  });

  it("going back to earlier parameters is served from the cache", async () => {
    const f = stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("ema");
    await s.refresh(ctx());
    s.update("ema-1", { params: { length: 50, source: "close" } });
    await s.refresh(ctx());
    s.update("ema-1", { params: { length: 20, source: "close" } });
    await s.refresh(ctx());
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("switching timeframe back and forth does not refetch", async () => {
    const f = stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("ema");
    await s.refresh(ctx({ timeframe: "15m" }));
    await s.refresh(ctx({ timeframe: "5m" }));
    await s.refresh(ctx({ timeframe: "15m" }));
    await s.refresh(ctx({ timeframe: "5m" }));
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("refetches for other sessions (a different series), but not twice", async () => {
    const f = stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("ema");
    await s.refresh(ctx());
    await s.refresh(ctx({ sessions: ["normal"] }));
    await s.refresh(ctx({ sessions: ["normal"] }));
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("a prepended candle chunk fetches only the older range and merges it in order", async () => {
    const f = stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("ema");
    await s.refresh(ctx());
    await s.refresh(ctx({ candles: [candle(400), candle(700), ...candles] }));

    expect(f).toHaveBeenCalledTimes(2);
    const body = JSON.parse(String(f.mock.calls[1]?.[1]?.body)) as IndicatorsRequest;
    expect([body.from, body.to]).toEqual([400, 700]);
    const merged = entryOf("ema", { length: 20, source: "close" });
    expect(merged?.times).toEqual([400, 700, 1000, 1300]);
    expect(merged?.outputs["ema"]).toEqual([40, 70, 100, 130]);
  });

  it("values for bars that left the chart window are dropped, and fetched again when they come back", async () => {
    const f = stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("ema");
    await s.refresh(ctx({ candles: [candle(400), candle(700), candle(1000), candle(1300)] }));
    expect(entryOf("ema", { length: 20, source: "close" })?.times).toEqual([400, 700, 1000, 1300]);

    // the window moved forward: the oldest two bars were dropped from the chart
    await s.refresh(ctx());
    expect(entryOf("ema", { length: 20, source: "close" })?.times).toEqual([1000, 1300]);
    expect(f).toHaveBeenCalledTimes(1); // nothing new was needed

    // scrolling back: only the dropped range is requested
    await s.refresh(ctx({ candles: [candle(400), candle(700), ...candles] }));
    expect(f).toHaveBeenCalledTimes(2);
    const body = JSON.parse(String(f.mock.calls[1]?.[1]?.body)) as IndicatorsRequest;
    expect([body.from, body.to]).toEqual([400, 700]);
    expect(entryOf("ema", { length: 20, source: "close" })?.times).toEqual([400, 700, 1000, 1300]);
  });

  it("identical concurrent refreshes share one request", async () => {
    let release: (v: unknown) => void = () => {};
    const f = stubFetch((req) => new Promise((resolve) => (release = () => resolve(answer(req)))));
    const s = useIndicatorStore.getState();
    s.add("ema");
    const a = s.refresh(ctx());
    const b = s.refresh(ctx());
    expect(useIndicatorStore.getState().status).toBe("loading");
    release(undefined);
    await Promise.all([a, b]);
    expect(f).toHaveBeenCalledTimes(1);
    expect(useIndicatorStore.getState().status).toBe("ready");
  });

  it("two copies with identical parameters are computed once", async () => {
    const f = stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("ema");
    s.duplicate("ema-1");
    await s.refresh(ctx());
    const body = JSON.parse(String(f.mock.calls[0]?.[1]?.body)) as IndicatorsRequest;
    expect(body.indicators).toHaveLength(1);
  });

  it("never requests VWAP for a zero-volume symbol", async () => {
    const f = stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("vwap");
    s.add("ema");
    await s.refresh(ctx({ candles: candles.map((c) => ({ ...c, volume: 0 })) }));
    const body = JSON.parse(String(f.mock.calls[0]?.[1]?.body)) as IndicatorsRequest;
    expect(body.indicators.map((i) => i.type)).toEqual(["ema"]);
  });

  it("a slow response for an old scope still lands in that scope's cache, not in the current one", async () => {
    let releaseFirst: () => void = () => {};
    let call = 0;
    stubFetch((req) => {
      call++;
      if (call === 1) return new Promise((resolve) => (releaseFirst = () => resolve(answer(req))));
      return answer(req);
    });
    const s = useIndicatorStore.getState();
    s.add("ema");
    const first = s.refresh(ctx({ timeframe: "15m" }));
    await s.refresh(ctx({ timeframe: "5m" }));
    releaseFirst();
    await first;

    expect(entryOf("ema", { length: 20, source: "close" }, ctx({ timeframe: "5m" }))).toBeDefined();
    expect(entryOf("ema", { length: 20, source: "close" }, ctx({ timeframe: "15m" }))?.times).toEqual([1000, 1300]);
  });

  it("reports backend errors and caches nothing for the failed request", async () => {
    stubFetch(() => ({ detail: "VWAP needs volume" }), 422);
    useIndicatorStore.getState().add("ema");
    await useIndicatorStore.getState().refresh(ctx());
    const s = useIndicatorStore.getState();
    expect(s.status).toBe("error");
    expect(s.error).toMatch(/VWAP needs volume/);
    expect(entryOf("ema", { length: 20, source: "close" })).toBeUndefined();
  });

  it("retries after an error on the next refresh", async () => {
    stubFetch(() => ({ detail: "boom" }), 500);
    useIndicatorStore.getState().add("ema");
    await useIndicatorStore.getState().refresh(ctx());
    expect(useIndicatorStore.getState().status).toBe("error");
    const f = stubFetch(answer);
    await useIndicatorStore.getState().refresh(ctx());
    expect(f).toHaveBeenCalledTimes(1);
    expect(useIndicatorStore.getState().status).toBe("ready");
  });

  it("evicts the oldest scopes beyond the cache limit", async () => {
    stubFetch(answer);
    const s = useIndicatorStore.getState();
    s.add("ema");
    for (let n = 0; n < 15; n++) await s.refresh(ctx({ symbol: `SYM${n}` }));
    expect(Object.keys(useIndicatorStore.getState().data).length).toBeLessThanOrEqual(12);
  });
});

describe("persisted state is the only thing kept", () => {
  it("createInstance defaults are what the store adds", () => {
    useIndicatorStore.getState().add("supertrend");
    expect(useIndicatorStore.getState().items[0]).toEqual(createInstance("supertrend", []));
  });
});
