import { describe, expect, it } from "vitest";

import {
  INITIAL_VISIBLE_BARS,
  autoReset,
  chartHidden,
  defaultVisibleRange,
  isAwayFromLive,
} from "./controls";

describe("chartHidden (tab switch)", () => {
  it("hides the chart for Options without implying an unmount", () => {
    // Remounting ChartView was the root cause of jumping to a past date.
    expect(chartHidden("chart")).toBe(false);
    expect(chartHidden("options")).toBe(true);
  });
});

describe("isAwayFromLive / Auto reset", () => {
  it("treats a right edge short of the last bars as scrolled away", () => {
    expect(isAwayFromLive({ from: 0, to: 100 }, 200)).toBe(true);
    expect(isAwayFromLive({ from: 50, to: 200 }, 200)).toBe(false);
    expect(isAwayFromLive({ from: 50, to: 205 }, 200)).toBe(false);
    expect(isAwayFromLive(null, 200)).toBe(false);
  });

  it("is always away when the loaded window dropped newer bars", () => {
    expect(isAwayFromLive({ from: 50, to: 205 }, 200, true)).toBe(true);
  });

  it("Auto reset restores auto-scale and the default latest-candle window", () => {
    const reset = autoReset(2000);
    expect(reset.autoScale).toBe(true);
    expect(reset.barSpacing).toBe(6);
    expect(reset.rightOffset).toBe(5);
    expect(reset.range).toEqual(defaultVisibleRange(2000));
    expect(reset.range).toEqual({
      from: 2000 - INITIAL_VISIBLE_BARS,
      to: 2000 + 5,
    });
  });
});
