import { describe, expect, it } from "vitest";

import type { Candle } from "../api/client";
import {
  LEGEND_COLLAPSED_KEY,
  indexOfTime,
  initialRange,
  loadLegendCollapsed,
  needsNewer,
  needsOlder,
  prependedCount,
  saveLegendCollapsed,
  shiftRange,
  snapshotOf,
  windowShift,
} from "./view";

const candle = (time: number): Candle => ({ time, open: 1, high: 2, low: 0, close: 1, volume: 0, oi: null });
const run = (from: number, to: number): Candle[] => Array.from({ length: to - from + 1 }, (_, i) => candle((from + i) * 300));

describe("prependedCount", () => {
  const shown = run(100, 109);
  const prev = snapshotOf("A", shown);

  it("counts the older candles added at the left", () => {
    expect(prependedCount(prev, "A", [...run(90, 99), ...shown])).toBe(10);
  });

  it("is 0 on first load, for another scope, a new newest bar, or no change", () => {
    expect(prependedCount(null, "A", shown)).toBe(0);
    expect(prependedCount(prev, "B", [...run(90, 99), ...shown])).toBe(0);
    expect(prependedCount(prev, "A", [...run(90, 99), ...shown, candle(110 * 300)])).toBe(0);
    expect(prependedCount(prev, "A", shown)).toBe(0);
    expect(prependedCount(prev, "A", [])).toBe(0);
  });

  it("is 0 when the old first candle is gone (a different series)", () => {
    expect(prependedCount(prev, "A", run(101, 109))).toBe(0);
  });
});

describe("visible range handling", () => {
  it("shiftRange keeps the same bars on screen after n bars were prepended", () => {
    // 4000 candles were added: what was bar 1850..2005 is now bar 5850..6005
    expect(shiftRange({ from: 1850, to: 2005 }, 4000)).toEqual({ from: 5850, to: 6005 });
  });

  it("needsOlder triggers within a screenful (min 300 bars) of the left edge", () => {
    expect(needsOlder({ from: 1850, to: 2005 })).toBe(false);
    expect(needsOlder({ from: 299, to: 450 })).toBe(true);
    expect(needsOlder({ from: -20, to: 130 })).toBe(true);
    // zoomed far out: a wide view needs a wider margin
    expect(needsOlder({ from: 900, to: 2000 })).toBe(true);
    expect(needsOlder({ from: 1200, to: 2000 })).toBe(false);
  });

  it("initialRange shows the last N bars with air on the right", () => {
    expect(initialRange(2000, 150)).toEqual({ from: 1850, to: 2005 });
    expect(initialRange(10, 150)).toEqual({ from: 0, to: 15 });
  });
});

describe("indexOfTime", () => {
  const list = run(10, 19);
  it("finds exact candle starts only", () => {
    expect(indexOfTime(list, 13 * 300)).toBe(3);
    expect(indexOfTime(list, 13 * 300 + 1)).toBeUndefined();
    expect(indexOfTime(list, 0)).toBeUndefined();
    expect(indexOfTime([], 5)).toBeUndefined();
  });
});

describe("legend collapsed preference", () => {
  it("round-trips through storage and tolerates a missing / broken one", () => {
    const data = new Map<string, string>();
    const storage = { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) };
    expect(loadLegendCollapsed(storage)).toBe(false);
    saveLegendCollapsed(true, storage);
    expect(data.get(LEGEND_COLLAPSED_KEY)).toBe("1");
    expect(loadLegendCollapsed(storage)).toBe(true);
    saveLegendCollapsed(false, storage);
    expect(loadLegendCollapsed(storage)).toBe(false);
    expect(loadLegendCollapsed(null)).toBe(false);
    expect(() => saveLegendCollapsed(true, null)).not.toThrow();
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(loadLegendCollapsed(broken)).toBe(false);
    expect(() => saveLegendCollapsed(true, broken)).not.toThrow();
  });
});

describe("windowShift (the window slides in either direction)", () => {
  const shown = run(100, 109); // 10 bars
  const prev = snapshotOf("A", shown);

  it("older bars added on the left: positive shift", () => {
    expect(windowShift(prev, "A", [...run(90, 99), ...shown])).toBe(10);
  });

  it("older bars added on the left AND newest bars dropped on the right: still the left shift", () => {
    expect(windowShift(prev, "A", [...run(90, 99), ...run(100, 104)])).toBe(10);
  });

  it("newer bars appended and oldest dropped on the left: negative shift", () => {
    // bars 100..103 dropped; 110..119 added -> old first is gone, old last (109) now at index 5
    const next = run(104, 119);
    expect(windowShift(prev, "A", next)).toBe(-4);
  });

  it("a bar appended on the right only: shift 0", () => {
    expect(windowShift(prev, "A", [...shown, candle(110 * 300)])).toBe(0);
  });

  it("is null for another scope, first load, empty data or no overlap", () => {
    expect(windowShift(null, "A", shown)).toBeNull();
    expect(windowShift(prev, "B", shown)).toBeNull();
    expect(windowShift(prev, "A", [])).toBeNull();
    expect(windowShift(prev, "A", run(500, 510))).toBeNull();
  });

  it("keeps the same bars on screen when combined with shiftRange", () => {
    const next = run(104, 119); // dropped 4 on the left
    const shift = windowShift(prev, "A", next);
    expect(shift).toBe(-4);
    // the bar that was at logical index 6 (time 106*300) must still be at the shifted index
    const range = shiftRange({ from: 6, to: 9 }, shift ?? 0);
    expect(next[range.from]?.time).toBe(106 * 300);
  });
});

describe("needsNewer", () => {
  it("triggers within a screenful (min 300 bars) of the right edge of the loaded bars", () => {
    expect(needsNewer({ from: 100, to: 250 }, 60000)).toBe(false);
    expect(needsNewer({ from: 59600, to: 59750 }, 60000)).toBe(true);
    expect(needsNewer({ from: 59800, to: 60005 }, 60000)).toBe(true);
    // zoomed far out: a wide view needs a wider margin
    expect(needsNewer({ from: 50000, to: 59000 }, 60000)).toBe(true);
    expect(needsNewer({ from: 40000, to: 50000 }, 60000)).toBe(false);
  });
});
