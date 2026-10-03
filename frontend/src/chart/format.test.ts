import { describe, expect, it } from "vitest";

import type { Candle } from "../api/client";
import { formatCrosshairTime, formatTick, legendValues } from "./format";

// 2024-01-01 09:15 IST == 03:45 UTC == 1704080700 (a Monday)
const T = 1704080700;

describe("IST time formatting", () => {
  it("crosshair label is IST wall-clock for intraday timeframes", () => {
    expect(formatCrosshairTime(T, "15m")).toBe("Mon 01 Jan '24 09:15");
    expect(formatCrosshairTime(T + 6 * 3600, "1h")).toBe("Mon 01 Jan '24 15:15");
  });

  it("uses a three-letter month (ICU may say \"Sept\") - e.g. \"Tue 29 Sep '26 10:45\"", () => {
    const t = Date.UTC(2026, 8, 29, 5, 15) / 1000; // 10:45 IST
    expect(formatCrosshairTime(t, "15m")).toBe("Tue 29 Sep '26 10:45");
    expect(formatTick(t, "month")).toBe("Sep");
  });

  it("crosshair label is date-only for 1D and 1W", () => {
    expect(formatCrosshairTime(T, "1D")).toBe("Mon 01 Jan '24");
    expect(formatCrosshairTime(T, "1W")).toBe("Mon 01 Jan '24");
  });

  it("uses the IST date even when UTC is still the previous day", () => {
    // 2024-01-02 00:30 IST == 2024-01-01 19:00 UTC
    const t = Date.UTC(2024, 0, 1, 19, 0) / 1000;
    expect(formatCrosshairTime(t, "5m")).toBe("Tue 02 Jan '24 00:30");
  });

  it("axis ticks", () => {
    expect(formatTick(T, "time")).toBe("09:15");
    expect(formatTick(T, "day")).toBe("1");
    expect(formatTick(T, "month")).toBe("Jan");
    expect(formatTick(T, "year")).toBe("2024");
  });
});

const candle = (o: number, h: number, l: number, c: number, v = 0): Candle => ({
  time: T,
  open: o,
  high: h,
  low: l,
  close: c,
  volume: v,
  oi: null,
});

describe("legendValues", () => {
  it("formats OHLC and change vs previous close", () => {
    const lv = legendValues(candle(24000, 24100.5, 23950, 24075.25), 24000, false);
    expect(lv).toEqual({
      open: "24,000.00",
      high: "24,100.50",
      low: "23,950.00",
      close: "24,075.25",
      change: "+75.25",
      changePct: "+0.31%",
      up: true,
      volume: null,
    });
  });

  it("falls back to the open when there is no previous candle, and handles down moves", () => {
    const lv = legendValues(candle(100, 101, 90, 95), null, false);
    expect(lv.change).toBe("-5.00");
    expect(lv.changePct).toBe("-5.00%");
    expect(lv.up).toBe(false);
  });

  it("only includes volume when asked", () => {
    expect(legendValues(candle(1, 1, 1, 1, 1234), 1, true).volume).toBe("1,234");
    expect(legendValues(candle(1, 1, 1, 1, 1234), 1, false).volume).toBeNull();
  });
});
