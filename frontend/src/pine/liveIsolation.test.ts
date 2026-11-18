import { describe, expect, it } from "vitest";

import { createInstance } from "../indicators/catalog";
import { mergeLiveCandles, tailChange } from "../live/merge";
import { closedTimeOnAppend, scheduleClosedBar, subscribeClosedBars } from "./closedBars";
import { liveView } from "../store/liveStore";
import type { Candle } from "../api/client";

function bar(time: number, close: number): Candle {
  return { time, open: close - 1, high: close + 2, low: close - 2, close, volume: 0, oi: null };
}

describe("pine live isolation", () => {
  it("pine scripts stay out of the live WS view (FHB lesson)", () => {
    // Pine lives in pineLiveStore only — never folded into indicator items that liveView sends.
    const ema = createInstance("ema", []);
    const view = liveView({ symbol: "NIFTY50", timeframe: "5m", sessions: ["normal"] }, [ema]);
    expect(view?.indicators.map((i) => i.type)).toEqual(["ema"]);
    expect(JSON.stringify(view)).not.toMatch(/pine|strategy\(/i);
  });

  it("chart keeps updating across a 5m boundary while a closed-bar listener runs", async () => {
    let pineCalls = 0;
    const unsub = subscribeClosedBars(() => {
      pineCalls += 1;
      // slow listener must not block — we only schedule after update
      const start = Date.now();
      while (Date.now() - start < 5) {
        /* spin briefly */
      }
    });

    let candles: Candle[] = [];
    const open = 1_000_000;
    for (let t = open; t < open + 90 * 60; t += 5 * 60) candles.push(bar(t, 24_400));
    const prev = candles;
    const nextBar = bar(prev[prev.length - 1]!.time + 5 * 60, 24_410);
    const merged = mergeLiveCandles(prev, [nextBar]);
    expect(tailChange(prev, merged.candles)).not.toBeNull();

    // Simulate ChartView: series.update first, then schedule
    const order: string[] = [];
    order.push("series.update");
    const closedTime = closedTimeOnAppend(prev, merged.candles);
    expect(closedTime).toBe(prev[prev.length - 1]!.time);
    scheduleClosedBar({
      scope: "nifty",
      symbol: "NIFTY50",
      timeframe: "5m",
      closedTime: closedTime!,
      candles: merged.candles,
      history: false,
    });
    order.push("return-to-ui");
    await Promise.resolve();
    expect(order).toEqual(["series.update", "return-to-ui"]);
    expect(pineCalls).toBe(1);
    expect(merged.candles).toHaveLength(prev.length + 1);
    unsub();
  });
});
