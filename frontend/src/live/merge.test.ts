import { describe, expect, it } from "vitest";

import type { Candle } from "../api/client";
import { mergeLiveCandles, tailChange } from "./merge";

const c = (time: number, close = 1, volume = 0): Candle => ({ time, open: 1, high: 2, low: 0, close, volume, oi: null });

describe("mergeLiveCandles", () => {
  const base = [c(100), c(200), c(300)];

  it("replaces the newest candle in place", () => {
    const r = mergeLiveCandles(base, [c(300, 5)]);
    expect(r.changed).toBe(true);
    expect(r.candles.map((x) => x.close)).toEqual([1, 1, 5]);
    expect(r.candles[0]).toBe(base[0]); // untouched candles keep identity
    expect(base[2]?.close).toBe(1); // input not mutated
  });

  it("appends a newer candle", () => {
    const r = mergeLiveCandles(base, [c(300, 2), c(400, 3)]);
    expect(r.candles.map((x) => x.time)).toEqual([100, 200, 300, 400]);
    expect(r.candles[2]?.close).toBe(2);
  });

  it("returns the same array when nothing changed", () => {
    const r = mergeLiveCandles(base, [c(200), c(300)]);
    expect(r.changed).toBe(false);
    expect(r.candles).toBe(base);
  });

  it("replaces an older loaded candle, ignores one that is not loaded", () => {
    expect(mergeLiveCandles(base, [c(200, 9)]).candles[1]?.close).toBe(9);
    expect(mergeLiveCandles(base, [c(150, 9)]).changed).toBe(false);
  });

  it("a volume or oi change counts as a change", () => {
    expect(mergeLiveCandles(base, [c(300, 1, 7)]).changed).toBe(true);
  });

  it("an empty window just takes the candles", () => {
    expect(mergeLiveCandles([], [c(100)]).candles).toHaveLength(1);
  });
});

describe("tailChange", () => {
  const base = [c(100), c(200), c(300), c(400)];

  it("detects a replaced newest candle and returns its index", () => {
    expect(tailChange(base, mergeLiveCandles(base, [c(400, 5)]).candles)).toBe(3);
  });

  it("detects an appended candle: first index that may differ is the old newest", () => {
    expect(tailChange(base, mergeLiveCandles(base, [c(400, 2), c(500)]).candles)).toBe(3);
  });

  it("is null for a different data set, a prepend, or a changed earlier candle", () => {
    expect(tailChange(base, [c(100), c(200)])).toBeNull();
    expect(tailChange(base, [c(50), ...base])).toBeNull();
    expect(tailChange(base, mergeLiveCandles(base, [c(300, 9)]).candles)).toBeNull();
    expect(tailChange([], base)).toBeNull();
    expect(tailChange(base, [...base, c(500), c(600)])).toBeNull();
  });
});
