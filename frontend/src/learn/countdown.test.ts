import { describe, expect, it } from "vitest";

import { axisCountdown, countdownLine, hhmm, mmss, nextBarClose } from "./countdown";
import { istEpoch } from "./time";

describe("Learn countdown", () => {
  it("counts the 5-minute close, the session close, and the next listed event", () => {
    const now = istEpoch(2026, 11, 26, 10, 7);
    expect(nextBarClose(now, 300)).toBe(istEpoch(2026, 11, 26, 10, 10));
    expect(mmss(90)).toBe("01:30");
    expect(hhmm(3700)).toBe("1h 01m");
    const line = countdownLine(now, "5m", 300, false, "NIFTY50");
    expect(line).toMatch(/^5m candle closes in 03:00 \(at 10:10 IST — signals are checked on 5m closes\) · Market closes in 5h 23m \(15:30\) · /);
    expect(axisCountdown(now, 300, false)).toBe("03:00");
    expect(axisCountdown(now, 300, true)).toBeNull();
    expect(countdownLine(now, "5m", 300, true, "NIFTY50")).toBe("Replay — countdowns pause in replay");
    expect(countdownLine(istEpoch(2026, 11, 27, 18, 0), "5m", 300, false, "NIFTY50")).toMatch(/^Market closed · /);
  });
});
