import { describe, expect, it } from "vitest";

import { candleCloseUnix, candleCountdown, candleOpenUnix, formatCountdown } from "./countdown";

/** IST wall clock → unix seconds (same helper style as ai/auto.test). */
function ist(year: number, month: number, day: number, hour: number, minute: number, second = 0): number {
  return Math.floor(Date.UTC(year, month - 1, day, hour, minute, second) / 1000 - 5.5 * 3600);
}

const CAL = {
  holidays: ["2026-10-02"],
  weekendSessions: ["2026-02-01"],
};

describe("candleOpenUnix", () => {
  it("keeps the final short 1h bar at 15:15 (draw barStart would clamp it away)", () => {
    expect(candleOpenUnix(ist(2026, 10, 1, 15, 20), "1h")).toBe(ist(2026, 10, 1, 15, 15));
    expect(candleOpenUnix(ist(2026, 10, 1, 14, 20), "1h")).toBe(ist(2026, 10, 1, 14, 15));
    expect(candleOpenUnix(ist(2026, 10, 1, 15, 29), "1m")).toBe(ist(2026, 10, 1, 15, 29));
  });
});

describe("candleCloseUnix", () => {
  it("ends intraday bars on the step, except the last bar clamps to 15:30", () => {
    // Thursday 2026-10-01
    expect(candleCloseUnix(ist(2026, 10, 1, 9, 15), "1m")).toBe(ist(2026, 10, 1, 9, 16));
    expect(candleCloseUnix(ist(2026, 10, 1, 10, 0), "5m")).toBe(ist(2026, 10, 1, 10, 5));
    expect(candleCloseUnix(ist(2026, 10, 1, 10, 0), "15m")).toBe(ist(2026, 10, 1, 10, 15));
    expect(candleCloseUnix(ist(2026, 10, 1, 10, 15), "1h")).toBe(ist(2026, 10, 1, 11, 15));
    // 15:15 1h candle ends at 15:30, not 16:15
    expect(candleCloseUnix(ist(2026, 10, 1, 15, 15), "1h")).toBe(ist(2026, 10, 1, 15, 30));
    expect(candleCloseUnix(ist(2026, 10, 1, 15, 15), "15m")).toBe(ist(2026, 10, 1, 15, 30));
    expect(candleCloseUnix(ist(2026, 10, 1, 15, 25), "5m")).toBe(ist(2026, 10, 1, 15, 30));
    expect(candleCloseUnix(ist(2026, 10, 1, 15, 29), "1m")).toBe(ist(2026, 10, 1, 15, 30));
  });

  it("ends a daily bar at 15:30 IST", () => {
    expect(candleCloseUnix(ist(2026, 10, 1, 9, 15), "1D")).toBe(ist(2026, 10, 1, 15, 30));
  });
});

describe("formatCountdown", () => {
  it("uses mm:ss for short timeframes and hh:mm:ss for 1h+", () => {
    expect(formatCountdown(65, "5m")).toBe("01:05");
    expect(formatCountdown(65, "1h")).toBe("00:01:05");
    expect(formatCountdown(3661, "1D")).toBe("01:01:01");
  });
});

describe("candleCountdown", () => {
  it("counts down 1m / 5m / 15m / 1h / daily including the 15:15→15:30 edge", () => {
    const at = (h: number, m: number, s = 0) => ist(2026, 10, 1, h, m, s);

    expect(candleCountdown(at(9, 15, 30), "1m", CAL)).toEqual({
      kind: "open",
      text: "00:30",
      secondsLeft: 30,
    });
    expect(candleCountdown(at(10, 2, 0), "5m", CAL)).toEqual({
      kind: "open",
      text: "03:00",
      secondsLeft: 180,
    });
    expect(candleCountdown(at(10, 10, 0), "15m", CAL)).toEqual({
      kind: "open",
      text: "05:00",
      secondsLeft: 300,
    });
    // Mid 10:15 1h bar
    expect(candleCountdown(at(10, 45, 0), "1h", CAL)).toEqual({
      kind: "open",
      text: "00:30:00",
      secondsLeft: 1800,
    });
    // Last 1h bar of the day: 15:15 → 15:30
    expect(candleCountdown(at(15, 20, 0), "1h", CAL)).toEqual({
      kind: "open",
      text: "00:10:00",
      secondsLeft: 600,
    });
    // Daily → 15:30
    expect(candleCountdown(at(10, 0, 0), "1D", CAL)).toEqual({
      kind: "open",
      text: "05:30:00",
      secondsLeft: 5 * 3600 + 30 * 60,
    });
  });

  it("shows Closed outside hours, on holidays, and after the session", () => {
    expect(candleCountdown(ist(2026, 10, 1, 9, 0), "15m", CAL).text).toBe("Closed");
    expect(candleCountdown(ist(2026, 10, 1, 15, 30), "15m", CAL).text).toBe("Closed");
    expect(candleCountdown(ist(2026, 10, 1, 20, 0), "1h", CAL).text).toBe("Closed");
    expect(candleCountdown(ist(2026, 10, 2, 10, 0), "15m", CAL).text).toBe("Closed"); // holiday
    expect(candleCountdown(ist(2026, 10, 3, 10, 0), "15m", CAL).text).toBe("Closed"); // Saturday
  });

  it("runs on a listed full weekend session", () => {
    const c = candleCountdown(ist(2026, 2, 1, 10, 0), "15m", CAL);
    expect(c.kind).toBe("open");
    expect(c.text).toBe("15:00");
  });
});
