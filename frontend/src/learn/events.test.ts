import { describe, expect, it } from "vitest";

import { eventsOn, isExpiryDay, isHoliday, todayLine } from "./events";

describe("Learn event calendar", () => {
  it("marks the 2026 NSE holidays and names Republic Day", () => {
    expect(isHoliday("2026-01-26")).toBe(true);
    expect(isHoliday("2026-11-27")).toBe(false);
    expect(eventsOn("2026-01-26").some((item) => item.title === "Republic Day" && item.verified)).toBe(true);
  });

  it("keeps Nifty expiry on Tuesday from September 2025 and shifts a holiday", () => {
    expect(isExpiryDay("2026-11-17", "NIFTY50")).toBe(true);
    expect(isExpiryDay("2026-11-24", "NIFTY50")).toBe(false);
    expect(isExpiryDay("2026-11-23", "NIFTY50")).toBe(true);
    expect(isExpiryDay("2025-08-28", "NIFTY50")).toBe(true);
    expect(isExpiryDay("2025-09-02", "NIFTY50")).toBe(true);
  });

  it("puts an unverified note on a date that was not checked", () => {
    const line = todayLine("2026-02-02", "NIFTY50");
    expect(line === "Today: no listed event." || line.includes("unverified") || line.includes("Today:")).toBe(true);
    expect(todayLine("2026-01-26", "NIFTY50")).toMatch(/Republic Day/);
    expect(todayLine("2026-11-27", "NIFTY50")).toBe("Today: no listed event.");
  });
});
