import { describe, expect, it } from "vitest";

import type { Candle } from "../api/client";
import { acceptBars, capCandlesQuery, capIndicatorsRequest } from "./cap";

const candle = (time: number): Candle => ({ time, open: 1, high: 1, low: 1, close: 1, volume: 1, oi: null });

describe("replay request cap", () => {
  it("caps candle pages, lazy loads, and indicator requests at the cursor", () => {
    expect(capCandlesQuery({ symbol: "NIFTY50", timeframe: "5m", limit: 100 }, 300).cursor).toBe(300);
    expect(capCandlesQuery({ symbol: "NIFTY50", timeframe: "5m", limit: 100 }, 300).to).toBeUndefined();
    expect(capCandlesQuery({ symbol: "NIFTY50", timeframe: "5m", from: 0, to: 900 }, 300).to).toBe(300);
    const newer = capCandlesQuery({ symbol: "NIFTY50", timeframe: "5m", limit: 10, after: 400 }, 300);
    expect(newer.after).toBeUndefined();
    expect(newer.to).toBe(300);
    expect(
      capIndicatorsRequest(
        { symbol: "NIFTY50", timeframe: "5m", from: 0, to: 900, indicators: [] },
        300,
      ).to,
    ).toBe(300);
  });

  it("rejects a response that contains a bar after the cursor", () => {
    expect(acceptBars([candle(100), candle(300)], 300).map((bar) => bar.time)).toEqual([100, 300]);
    expect(() => acceptBars([candle(100), candle(301)], 300)).toThrow(/after the cursor/);
  });
});
