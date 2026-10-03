import { describe, expect, it } from "vitest";

import type { Candle } from "../api/client";
import type { IndicatorEntry } from "./cache";
import { createInstance, type IndicatorInstance } from "./catalog";
import { legendRows } from "./legend";

const candle = (time: number, volume = 100): Candle => ({ time, open: 1, high: 2, low: 0, close: 1, volume, oi: null });
const candles = [candle(1000), candle(1300), candle(1600)];

const ema = createInstance("ema", []);
const st = createInstance("supertrend", []);
const macd = createInstance("macd", []);
const vwap = createInstance("vwap", []);
const rsi = createInstance("rsi", []);

const times = [1000, 1300, 1600];
const entries: Record<string, IndicatorEntry> = {
  [ema.id]: { times, outputs: { ema: [null, 24500.5, 24600.25] } },
  [st.id]: { times, outputs: { supertrend: [null, 100, 110], direction: [null, 1, -1] } },
  [macd.id]: { times, outputs: { macd: [0, 1, -2], signal: [0, 1, -1], hist: [0, 0, -1] } },
  [rsi.id]: { times, outputs: { rsi: [null, null, 55.5] } },
};

const base = { entryFor: (i: IndicatorInstance) => entries[i.id], candles, timeframe: "15m" as const };

describe("legendRows", () => {
  it("shows the value at the given candle, formatted", () => {
    const rows = legendRows({ ...base, items: [ema], time: 1600 });
    expect(rows).toHaveLength(1);
    expect(rows[0]?.name).toBe("EMA (20, close)");
    expect(rows[0]?.entries.map((e) => e.text)).toEqual(["24,600.25"]);
    expect(rows[0]?.entries[0]?.color).toBe(ema.colors["line"]);
  });

  it("follows the crosshair index and shows a dash while warming up", () => {
    expect(legendRows({ ...base, items: [ema], time: 1300 })[0]?.entries[0]?.text).toBe("24,500.50");
    expect(legendRows({ ...base, items: [ema], time: 1000 })[0]?.entries[0]?.text).toBe("–");
  });

  it("Supertrend takes the up / down colour from the direction (-1 = up)", () => {
    const down = legendRows({ ...base, items: [st], time: 1300 })[0]?.entries[0];
    const up = legendRows({ ...base, items: [st], time: 1600 })[0]?.entries[0];
    expect([down?.label, down?.color]).toEqual(["down", st.colors["down"]]);
    expect([up?.label, up?.color]).toEqual(["up", st.colors["up"]]);
  });

  it("MACD shows macd / signal / hist and colours the histogram by sign", () => {
    const entries = legendRows({ ...base, items: [macd], time: 1600 })[0]?.entries ?? [];
    expect(entries.map((e) => [e.label, e.text])).toEqual([["macd", "-2.00"], ["signal", "-1.00"], ["hist", "-1.00"]]);
    expect(entries[2]?.color).toBe(macd.colors["histDown"]);
  });

  it("hidden indicators are omitted", () => {
    expect(legendRows({ ...base, items: [{ ...ema, visible: false }], time: 1600 })).toEqual([]);
  });

  it("VWAP on zero-volume data is listed as unavailable, with no values", () => {
    const zero = candles.map((c) => ({ ...c, volume: 0 }));
    const [row] = legendRows({ ...base, candles: zero, items: [vwap], time: 1600 });
    expect(row?.unavailable).toMatch(/volume/i);
    expect(row?.entries).toEqual([]);
  });

  it("shows a dash for a time the cache does not cover (older chunk not loaded yet)", () => {
    const [row] = legendRows({ ...base, items: [ema], time: 50 });
    expect(row?.entries.map((e) => e.text)).toEqual(["–"]);
  });

  it("shows no values while an indicator has no cached data at all", () => {
    const [row] = legendRows({ ...base, entryFor: () => undefined, items: [ema], time: 1600 });
    expect(row?.entries).toEqual([]);
  });
});
