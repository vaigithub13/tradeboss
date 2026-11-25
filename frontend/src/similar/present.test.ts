import { describe, expect, it } from "vitest";

import { forwardEndTime, predictionLabel, reliabilityLine, reviewLine, snapshotMarkers, summaryLine, verdictClass } from "./present";

describe("similar pattern honesty", () => {
  it("hides verdict colour until there are 10 samples", () => {
    expect(verdictClass(9, "above the base rate")).toBe("text-white/70");
    expect(verdictClass(10, "above the base rate")).toBe("text-emerald-300");
    expect(verdictClass(40, "no better than coin flip")).toBe("text-white/70");
    expect(verdictClass(40, "below the base rate")).toBe("text-rose-300");
  });

  it("puts the base rate beside the match rate", () => {
    const line = summaryLine("10", {
      matches: { n: 10, pct_up: 60, median_return: 0.12, spread: 0.4 },
      base: { n: 800, pct_up: 52, median_return: 0.01, spread: 0.3 },
    });
    expect(line).toContain("60% up");
    expect(line).toContain("base 52% of 800");
  });

  it("states a coin-flip walk-forward with its sample size", () => {
    const line = reliabilityLine({
      n: 400,
      hit_rate: 51,
      base_rate: 52,
      up_rate: 52,
      verdict: "no better than coin flip",
      sample_size: 900,
      evaluated: 900,
      runtime_s: 12.4,
      capped: false,
    });
    expect(line).toContain("no better than coin flip");
    expect(line).toContain("900/900");
    expect(line).toContain("12.4s");
  });

  it("labels a snapshot with the match count and refuses a candle that is not closed", () => {
    expect(predictionLabel(7, 10)).toBe("7/10 up");
    const marks = snapshotMarkers([
      { query: { end: 100 }, direction: "up", up_count: 7, match_n: 10, kind: "candles" },
      { query: { end: 100 }, direction: "down", up_count: 4, match_n: 20, kind: "days" },
    ]);
    expect(marks).toEqual([{ time: 100, direction: "up", text: "7/10 up" }]);
    expect(forwardEndTime([100, 200, 300, 400], 100, 5, 400)).toBeNull();
    expect(forwardEndTime([100, 200, 300, 400, 500, 600], 100, 5, 600)).toBe(600);
    expect(forwardEndTime([100, 200, 300, 400, 500, 600], 100, 5, 500)).toBeNull();
  });

  it("keeps the review verdict off until 50 predictions", () => {
    const thin = reviewLine({ predictions: 49, hits: 40, hit_rate: 80, base_rate: 50, points: 12, verdict: "better than base rate" });
    expect(thin).not.toContain("better than base rate");
    const ready = reviewLine({ predictions: 50, hits: 40, hit_rate: 80, base_rate: 50, points: 12.5, verdict: "better than base rate" });
    expect(ready).toContain("better than base rate");
    expect(ready).toContain("12.5 pts");
  });
});
