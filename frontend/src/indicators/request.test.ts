import { describe, expect, it } from "vitest";

import type { Candle } from "../api/client";
import type { IndicatorEntry } from "./cache";
import { indicatorKey } from "./cache";
import { createInstance, type IndicatorInstance } from "./catalog";
import { activeItems, buildIndicatorsRequest, planFetches, type RequestContext } from "./request";

const candle = (time: number, volume = 100): Candle => ({ time, open: 1, high: 2, low: 0, close: 1, volume, oi: null });
const candles = [candle(1000), candle(1300), candle(1600)];
const zeroVolume = candles.map((c) => ({ ...c, volume: 0 }));

const ema = createInstance("ema", []);
const vwap = createInstance("vwap", []);

const ctx = (over: Partial<RequestContext> = {}): RequestContext => ({
  symbol: "NIFTY50",
  timeframe: "15m",
  sessions: ["normal", "weekend_full"],
  candles,
  items: [ema],
  ...over,
});

const entryOf = (times: number[]): IndicatorEntry => ({ times, outputs: { ema: times.map(() => 1) } });

describe("planFetches", () => {
  it("asks for the whole loaded range when nothing is cached", () => {
    const [g, ...rest] = planFetches(ctx(), {});
    expect(rest).toHaveLength(0);
    expect(g?.range).toEqual({ from: 1000, to: 1600 });
    expect(g?.indicators.map((i) => i.type)).toEqual(["ema"]);
  });

  it("asks for nothing when the cache already covers the candles", () => {
    const key = indicatorKey("ema", ema.params);
    expect(planFetches(ctx(), { [key]: entryOf([1000, 1300, 1600]) })).toEqual([]);
  });

  it("asks only for the older candles when a chunk was prepended", () => {
    const older = [candle(100), candle(400), candle(700), ...candles];
    const key = indicatorKey("ema", ema.params);
    const groups = planFetches(ctx({ candles: older }), { [key]: entryOf([1000, 1300, 1600]) });
    expect(groups.map((g) => g.range)).toEqual([{ from: 100, to: 700 }]);
  });

  it("two copies with the same parameters share one computation; different parameters do not", () => {
    const copy: IndicatorInstance = { ...ema, id: "ema-2", colors: { line: "#000000" } };
    const ema50: IndicatorInstance = { ...ema, id: "ema-3", params: { length: 50, source: "close" } };
    const [g] = planFetches(ctx({ items: [ema, copy, ema50] }), {});
    expect(g?.indicators.map((i) => i.params["length"])).toEqual([20, 50]);
  });

  it("only the changed indicator is re-requested (others stay cached)", () => {
    const rsi = createInstance("rsi", []);
    const cache = {
      [indicatorKey("ema", ema.params)]: entryOf([1000, 1300, 1600]),
      [indicatorKey("rsi", rsi.params)]: entryOf([1000, 1300, 1600]),
    };
    const changed: IndicatorInstance = { ...ema, params: { length: 21, source: "close" } };
    const groups = planFetches(ctx({ items: [changed, rsi] }), cache);
    expect(groups).toHaveLength(1);
    expect(groups[0]?.indicators.map((i) => i.type)).toEqual(["ema"]);
  });

  it("indicators missing different ranges go into separate requests", () => {
    const rsi = createInstance("rsi", []);
    const older = [candle(100), ...candles];
    const cache = { [indicatorKey("ema", ema.params)]: entryOf([1000, 1300, 1600]) };
    const groups = planFetches(ctx({ candles: older, items: [ema, rsi] }), cache);
    expect(groups.map((g) => [g.range.from, g.range.to, g.indicators.map((i) => i.type)])).toEqual([
      [100, 100, ["ema"]],
      [100, 1600, ["rsi"]],
    ]);
  });

  it("never plans VWAP for zero-volume data or 1D / 1W", () => {
    expect(planFetches(ctx({ candles: zeroVolume, items: [vwap] }), {})).toEqual([]);
    expect(planFetches(ctx({ timeframe: "1D", items: [vwap] }), {})).toEqual([]);
    expect(planFetches(ctx({ timeframe: "1W", items: [vwap] }), {})).toEqual([]);
    expect(planFetches(ctx({ items: [vwap] }), {})[0]?.indicators).toHaveLength(1);
  });

  it("plans nothing without candles or items", () => {
    expect(planFetches(ctx({ candles: [] }), {})).toEqual([]);
    expect(planFetches(ctx({ items: [] }), {})).toEqual([]);
  });
});

describe("buildIndicatorsRequest", () => {
  it("uses the chart's symbol / timeframe / session filter and positional ids", () => {
    const [g] = planFetches(ctx({ items: [ema, createInstance("rsi", [])] }), {});
    expect(g && buildIndicatorsRequest(ctx(), g)).toEqual({
      symbol: "NIFTY50",
      timeframe: "15m",
      from: 1000,
      to: 1600,
      sessions: ["normal", "weekend_full"],
      indicators: [
        { id: "0", type: "ema", params: { length: 20, source: "close" } },
        { id: "1", type: "rsi", params: { length: 14, source: "close" } },
      ],
    });
  });
});

describe("activeItems", () => {
  it("skips hidden and invalid items, keeps several copies of one type", () => {
    const hidden: IndicatorInstance = { ...ema, id: "ema-2", visible: false };
    const invalid: IndicatorInstance = { ...ema, id: "ema-3", params: { length: 0, source: "close" } };
    const ema50: IndicatorInstance = { ...ema, id: "ema-4", params: { length: 50, source: "close" } };
    expect(activeItems(ctx({ items: [ema, hidden, invalid, ema50] })).map((i) => i.id)).toEqual(["ema-1", "ema-4"]);
  });
});
