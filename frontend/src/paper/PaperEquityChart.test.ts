import { describe, expect, it } from "vitest";

import { equitySeries } from "./dashboard";
import { linePointsForSlot } from "./PaperEquityChart";
import { REAL_TRADE_ROWS } from "./realApiFixture";
import type { PaperTradeRow } from "./trades";

describe("PaperEquityChart data", () => {
  it("linePointsForSlot dedupes real-shaped duplicate exit times", () => {
    const series = equitySeries(REAL_TRADE_ROWS as PaperTradeRow[]);
    for (const slot of ["2", "4", "5", "6", "7"]) {
      const pts = linePointsForSlot(series, slot);
      const times = pts.map((p) => p.time);
      expect(times).toEqual([...times].sort((a, b) => Number(a) - Number(b)));
      expect(new Set(times).size).toBe(times.length);
      expect(pts.every((p) => Number.isFinite(p.time) && Number.isFinite(p.value))).toBe(true);
    }
  });
});
