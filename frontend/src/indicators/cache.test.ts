import { describe, expect, it } from "vitest";

import type { Candle } from "../api/client";
import { applyTail, indicatorKey, lowerBound, mergeEntry, missingRanges, scopeKey, trimEntry, valueAt, type IndicatorEntry } from "./cache";

const candle = (time: number): Candle => ({ time, open: 1, high: 2, low: 0, close: 1, volume: 0, oi: null });
const list = (...times: number[]): Candle[] => times.map(candle);
const entry = (times: number[], values: (number | null)[] = times): IndicatorEntry => ({ times, outputs: { v: values } });

describe("keys", () => {
  it("indicatorKey ignores parameter order and depends on type and values", () => {
    expect(indicatorKey("macd", { fast: 12, slow: 26, signal: 9, source: "close" })).toBe(
      indicatorKey("macd", { source: "close", signal: 9, slow: 26, fast: 12 }),
    );
    expect(indicatorKey("ema", { length: 20, source: "close" })).not.toBe(indicatorKey("ema", { length: 21, source: "close" }));
    expect(indicatorKey("sma", { length: 20, source: "close" })).not.toBe(indicatorKey("ema", { length: 20, source: "close" }));
  });

  it("scopeKey separates symbol, timeframe and session selection", () => {
    const base = scopeKey("NIFTY50", "15m", ["normal"]);
    expect(scopeKey("NIFTY50", "5m", ["normal"])).not.toBe(base);
    expect(scopeKey("BANKNIFTY", "15m", ["normal"])).not.toBe(base);
    expect(scopeKey("NIFTY50", "15m", ["normal", "muhurat"])).not.toBe(base);
  });
});

describe("lowerBound / valueAt", () => {
  it("binary search finds the first index >= t", () => {
    expect(lowerBound([10, 20, 30], 5)).toBe(0);
    expect(lowerBound([10, 20, 30], 20)).toBe(1);
    expect(lowerBound([10, 20, 30], 21)).toBe(2);
    expect(lowerBound([10, 20, 30], 99)).toBe(3);
    expect(lowerBound([], 1)).toBe(0);
  });

  it("valueAt returns the value only at an exact covered time", () => {
    const e = entry([10, 20, 30], [1, null, 3]);
    expect(valueAt(e, "v", 10)).toBe(1);
    expect(valueAt(e, "v", 20)).toBeNull();
    expect(valueAt(e, "v", 25)).toBeNull();
    expect(valueAt(e, "v", 99)).toBeNull();
    expect(valueAt(e, "nope", 10)).toBeNull();
    expect(valueAt(undefined, "v", 10)).toBeNull();
  });
});

describe("missingRanges", () => {
  const c = list(100, 200, 300, 400, 500);

  it("is everything without an entry, nothing without candles", () => {
    expect(missingRanges(c, undefined)).toEqual([{ from: 100, to: 500 }]);
    expect(missingRanges(c, entry([]))).toEqual([{ from: 100, to: 500 }]);
    expect(missingRanges([], undefined)).toEqual([]);
  });

  it("is nothing when the entry covers all candles", () => {
    expect(missingRanges(c, entry([100, 200, 300, 400, 500]))).toEqual([]);
    expect(missingRanges(c, entry([50, 100, 200, 300, 400, 500, 600]))).toEqual([]);
  });

  it("is the older candles when the entry starts later (prepend)", () => {
    expect(missingRanges(c, entry([300, 400, 500]))).toEqual([{ from: 100, to: 200 }]);
  });

  it("is the newer candles when the entry ends earlier (live append)", () => {
    expect(missingRanges(c, entry([100, 200, 300]))).toEqual([{ from: 400, to: 500 }]);
  });

  it("can be both sides", () => {
    expect(missingRanges(c, entry([300]))).toEqual([{ from: 100, to: 200 }, { from: 400, to: 500 }]);
  });
});

describe("mergeEntry", () => {
  it("adopts a chunk when there is no entry (and copies it)", () => {
    const chunk = { times: [1, 2], outputs: { v: [10, 20] as (number | null)[] } };
    const merged = mergeEntry(undefined, chunk);
    expect(merged).toEqual({ times: [1, 2], outputs: { v: [10, 20] } });
    expect(merged.times).not.toBe(chunk.times);
  });

  it("prepends an older chunk in order", () => {
    const merged = mergeEntry(entry([3, 4], [30, 40]), { times: [1, 2], outputs: { v: [10, 20] } });
    expect(merged).toEqual({ times: [1, 2, 3, 4], outputs: { v: [10, 20, 30, 40] } });
  });

  it("appends a newer chunk in order", () => {
    const merged = mergeEntry(entry([1, 2], [10, 20]), { times: [3, 4], outputs: { v: [30, 40] } });
    expect(merged).toEqual({ times: [1, 2, 3, 4], outputs: { v: [10, 20, 30, 40] } });
  });

  it("de-duplicates overlap and keeps existing values; a covered chunk changes nothing", () => {
    const base = entry([3, 4, 5], [30, 40, 50]);
    const overlap = mergeEntry(base, { times: [2, 3, 4], outputs: { v: [20, 31, 41] } });
    expect(overlap).toEqual({ times: [2, 3, 4, 5], outputs: { v: [20, 30, 40, 50] } });
    const inside = mergeEntry(base, { times: [4], outputs: { v: [99] } });
    expect(inside).toEqual(base);
  });

  it("keeps every output aligned with times", () => {
    const a: IndicatorEntry = { times: [3, 4], outputs: { x: [3, 4], y: [null, 8] } };
    const merged = mergeEntry(a, { times: [1, 2], outputs: { x: [1, 2], y: [null, null] } });
    expect(merged.times).toEqual([1, 2, 3, 4]);
    expect(merged.outputs["x"]).toEqual([1, 2, 3, 4]);
    expect(merged.outputs["y"]).toEqual([null, null, null, 8]);
  });

  it("does not mutate its inputs", () => {
    const base = entry([3, 4], [30, 40]);
    const snapshot = JSON.stringify(base);
    mergeEntry(base, { times: [1, 2], outputs: { v: [10, 20] } });
    expect(JSON.stringify(base)).toBe(snapshot);
  });
});

describe("trimEntry", () => {
  const e = entry([10, 20, 30, 40, 50], [1, 2, 3, 4, 5]);

  it("returns the same object when everything is inside", () => {
    expect(trimEntry(e, 0, 100)).toBe(e);
    expect(trimEntry(e, 10, 50)).toBe(e);
  });

  it("cuts both ends (inclusive) and keeps the outputs aligned with the times", () => {
    expect(trimEntry(e, 20, 40)).toEqual({ times: [20, 30, 40], outputs: { v: [2, 3, 4] } });
    expect(trimEntry(e, 25, 100)).toEqual({ times: [30, 40, 50], outputs: { v: [3, 4, 5] } });
    expect(trimEntry(e, 0, 29)).toEqual({ times: [10, 20], outputs: { v: [1, 2] } });
  });

  it("is undefined when nothing is left", () => {
    expect(trimEntry(e, 60, 90)).toBeUndefined();
    expect(trimEntry(e, 21, 29)).toBeUndefined();
  });

  it("a trimmed entry makes missingRanges ask for exactly the dropped part again", () => {
    const trimmed = trimEntry(e, 30, 50);
    expect(missingRanges(list(10, 20, 30, 40, 50), trimmed)).toEqual([{ from: 10, to: 20 }]);
  });
});

describe("applyTail", () => {
  const entry = { times: [100, 200, 300], outputs: { ema: [1, 2, 3] as (number | null)[] } };

  it("overwrites the newest value and appends a new bar", () => {
    const r = applyTail(entry, { times: [300, 400], outputs: { ema: [3.5, 4] } });
    expect(r).toEqual({ times: [100, 200, 300, 400], outputs: { ema: [1, 2, 3.5, 4] } });
    expect(entry.times).toEqual([100, 200, 300]); // input untouched
  });

  it("replaces the last two values when the chunk overlaps by two", () => {
    expect(applyTail(entry, { times: [200, 300], outputs: { ema: [2.5, 3.5] } })?.outputs.ema).toEqual([1, 2.5, 3.5]);
  });

  it("refuses chunks that would leave a hole, or that predate the entry, or when there is no entry", () => {
    expect(applyTail(entry, { times: [400], outputs: { ema: [4] } })).toBeUndefined();
    expect(applyTail(entry, { times: [50, 100], outputs: { ema: [0, 1] } })).toBeUndefined();
    expect(applyTail(undefined, { times: [100], outputs: { ema: [1] } })).toBeUndefined();
  });

  it("fills outputs the chunk does not carry with null", () => {
    const two = { times: [100, 200], outputs: { a: [1, 2] as (number | null)[], b: [3, 4] as (number | null)[] } };
    expect(applyTail(two, { times: [200], outputs: { a: [9] } })?.outputs).toEqual({ a: [1, 9], b: [3, null] });
  });
});
