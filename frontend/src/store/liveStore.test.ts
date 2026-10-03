import { beforeEach, describe, expect, it } from "vitest";

import type { Candle } from "../api/client";
import { createInstance } from "../indicators/catalog";
import { indicatorKey, scopeKey } from "../indicators/cache";
import type { BarMessage } from "../live/protocol";
import { clearChartCache, useChartStore } from "./chartStore";
import { clearIndicatorCache, useIndicatorStore } from "./indicatorStore";
import { applyBar, liveUrl, liveView } from "./liveStore";

const c = (time: number, close = 1): Candle => ({ time, open: 1, high: 2, low: 0, close, volume: 3, oi: null });
const SESSIONS = ["normal", "weekend_full"] as never;

beforeEach(() => {
  clearChartCache();
  clearIndicatorCache();
  useIndicatorStore.setState({ items: [] });
  useChartStore.setState({
    symbol: "NIFTY50",
    timeframe: "5m",
    sessions: SESSIONS,
    candles: [c(100), c(400), c(700)],
    loaded: { symbol: "NIFTY50", timeframe: "5m", sessions: SESSIONS },
    hasMoreOlder: false,
    hasMoreNewer: false,
    status: "ready",
  });
});

describe("chartStore.applyLive", () => {
  const apply = (symbol: string, tf: string, candles: Candle[]) => useChartStore.getState().applyLive(symbol, tf, candles);

  it("updates the newest candle and appends new ones", () => {
    expect(apply("NIFTY50", "5m", [c(700, 5), c(1000, 6)])).toBe(true);
    expect(useChartStore.getState().candles.map((x) => [x.time, x.close])).toEqual([[100, 1], [400, 1], [700, 5], [1000, 6]]);
  });

  it("ignores another symbol or timeframe", () => {
    expect(apply("RELIANCE", "5m", [c(700, 5)])).toBe(false);
    expect(apply("NIFTY50", "15m", [c(700, 5)])).toBe(false);
    expect(useChartStore.getState().candles[2]?.close).toBe(1);
  });

  it("ignores updates while the window is scrolled back (newer bars not loaded)", () => {
    useChartStore.setState({ hasMoreNewer: true });
    expect(apply("NIFTY50", "5m", [c(1000, 6)])).toBe(false);
    expect(useChartStore.getState().candles).toHaveLength(3);
  });

  it("does nothing, and keeps the same array, for an unchanged candle", () => {
    const before = useChartStore.getState().candles;
    expect(apply("NIFTY50", "5m", [c(700, 1)])).toBe(false);
    expect(useChartStore.getState().candles).toBe(before);
  });

  it("does nothing before anything is loaded", () => {
    useChartStore.setState({ loaded: null });
    expect(apply("NIFTY50", "5m", [c(1000)])).toBe(false);
  });
});

describe("indicatorStore.applyLiveTail", () => {
  const scope = scopeKey("NIFTY50", "5m", ["normal", "weekend_full"]);
  const seed = () =>
    useIndicatorStore.setState({
      data: { [scope]: { ema: { times: [100, 400, 700], outputs: { ema: [1, 2, 3] } } } },
    });

  it("overwrites the newest value and appends a new one", () => {
    seed();
    const ok = useIndicatorStore.getState().applyLiveTail(scope, [700, 1000], { ema: { ema: [3.5, 4] } });
    expect(ok).toBe(true);
    expect(useIndicatorStore.getState().data[scope]?.["ema"]).toEqual({ times: [100, 400, 700, 1000], outputs: { ema: [1, 2, 3.5, 4] } });
  });

  it("leaves unknown indicators and unconnected tails to the normal fetch", () => {
    seed();
    const s = useIndicatorStore.getState();
    expect(s.applyLiveTail(scope, [700], { other: { x: [1] } })).toBe(false);
    expect(s.applyLiveTail(scope, [1300], { ema: { ema: [9] } })).toBe(false);
    expect(s.applyLiveTail("nope", [700], { ema: { ema: [9] } })).toBe(false);
  });
});

describe("applyBar (what a live message does end to end)", () => {
  it("updates candles and indicator tail together, keyed by indicator key", () => {
    const item = createInstance("ema", []);
    const key = indicatorKey(item.type, item.params);
    const scope = scopeKey("NIFTY50", "5m", ["normal", "weekend_full"]);
    useIndicatorStore.setState({
      items: [item],
      data: { [scope]: { [key]: { times: [100, 400, 700], outputs: { ema: [1, 2, 3] } } } },
    });
    const msg: BarMessage = {
      type: "bar",
      symbol: "NIFTY50",
      timeframe: "5m",
      candles: [c(700, 9), c(1000, 8)],
      volume_known: true,
      times: [700, 1000],
      indicators: [{ id: key, outputs: { ema: [3.5, 4] } }],
    };
    applyBar(msg);
    expect(useChartStore.getState().candles.at(-1)).toMatchObject({ time: 1000, close: 8 });
    expect(useIndicatorStore.getState().data[scope]?.[key]?.outputs["ema"]).toEqual([1, 2, 3.5, 4]);
  });

  it("a bar for a symbol that is not on screen changes nothing", () => {
    const before = useChartStore.getState().candles;
    applyBar({ type: "bar", symbol: "OTHER", timeframe: "5m", candles: [c(1000)], volume_known: true });
    expect(useChartStore.getState().candles).toBe(before);
  });
});

describe("liveView / liveUrl", () => {
  it("describes the loaded data and the visible indicators, deduplicated", () => {
    const a = createInstance("ema", []);
    const b = { ...createInstance("ema", []), id: "ema-2" }; // same params => same key
    const hidden = { ...createInstance("rsi", []), id: "rsi-1", visible: false };
    const v = liveView({ symbol: "NIFTY50", timeframe: "5m", sessions: ["normal"] }, [a, b, hidden]);
    expect(v?.indicators).toHaveLength(1);
    expect(v?.indicators[0]?.id).toBe(indicatorKey(a.type, a.params));
    expect(v).toMatchObject({ symbol: "NIFTY50", timeframe: "5m", sessions: ["normal"] });
  });

  it("is null while nothing is loaded", () => {
    expect(liveView(null, [])).toBeNull();
  });

  it("uses ws on http and wss on https, through the same host (Vite proxy)", () => {
    expect(liveUrl({ protocol: "http:", host: "localhost:5173" })).toBe("ws://localhost:5173/api/live/ws");
    expect(liveUrl({ protocol: "https:", host: "x.y" })).toBe("wss://x.y/api/live/ws");
  });
});
