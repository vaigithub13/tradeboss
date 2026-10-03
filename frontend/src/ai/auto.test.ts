import { describe, expect, it } from "vitest";

import { autoDue, autoLabel, marketHours } from "./auto";

function ist(year: number, month: number, day: number, hour: number, minute: number): Date {
  return new Date(Date.UTC(year, month - 1, day, hour, minute) - 5.5 * 60 * 60 * 1000);
}

describe("auto-analyse", () => {
  it("runs on weekdays from 09:15 until 15:30 IST and stays off otherwise", () => {
    expect(marketHours(ist(2026, 10, 1, 9, 15))).toBe(true);
    expect(marketHours(ist(2026, 10, 1, 12, 45))).toBe(true);
    expect(marketHours(ist(2026, 10, 1, 15, 30))).toBe(false);
    expect(marketHours(ist(2026, 10, 1, 9, 14))).toBe(false);
    expect(marketHours(ist(2026, 10, 3, 10, 0))).toBe(false);
  });

  it("is off by default and waits a full interval during market hours", () => {
    expect(autoLabel({ language: "en", auto: false, auto_minutes: 15 })).toBe("Auto-analyse off");
    expect(autoLabel({ language: "en", auto: true, auto_minutes: 15 })).toBe(
      "Auto every 15 minutes during market hours",
    );
    const now = ist(2026, 10, 1, 10, 0);
    expect(autoDue(now, null, 15, true)).toBe(false);
    expect(autoDue(now, now.getTime(), 15, false)).toBe(false);
    expect(autoDue(now, now.getTime() - 14 * 60_000, 15, true)).toBe(false);
    expect(autoDue(now, now.getTime() - 15 * 60_000, 15, true)).toBe(true);
    expect(autoDue(ist(2026, 10, 1, 16, 0), now.getTime() - 15 * 60_000, 15, true)).toBe(false);
  });
});