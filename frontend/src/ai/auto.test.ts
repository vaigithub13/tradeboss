import { describe, expect, it } from "vitest";

import { autoDue, autoLabel, sessionOpen, type SessionCalendar } from "./auto";

function ist(year: number, month: number, day: number, hour: number, minute: number): Date {
  return new Date(Date.UTC(year, month - 1, day, hour, minute) - 5.5 * 60 * 60 * 1000);
}

/** The dates the timer is tested against. The API serves the same ones from the NSE calendar. */
const CALENDAR: SessionCalendar = {
  holidays: ["2026-10-02", "2025-10-21"],
  weekend_sessions: ["2026-02-01"],
  muhurat: ["2025-10-21"],
};

describe("auto-analyse sessions", () => {
  it("skips an NSE holiday and runs a full weekend session", () => {
    const holiday = ist(2026, 10, 2, 10, 0);
    const budgetSunday = ist(2026, 2, 1, 10, 0);
    expect(sessionOpen(holiday, CALENDAR, "unknown")).toBe(false);
    expect(sessionOpen(budgetSunday, CALENDAR, "unknown")).toBe(true);
    expect(sessionOpen(ist(2026, 2, 1, 9, 14), CALENDAR, "unknown")).toBe(false);
    expect(sessionOpen(ist(2026, 2, 1, 15, 30), CALENDAR, "unknown")).toBe(false);
  });

  it("follows market_info, including a Muhurat outside the regular window", () => {
    const thursday = ist(2026, 10, 1, 12, 45);
    const muhuratEvening = ist(2025, 10, 21, 18, 15);
    expect(sessionOpen(thursday, CALENDAR, "open")).toBe(true);
    expect(sessionOpen(thursday, CALENDAR, "closed")).toBe(false);
    expect(sessionOpen(muhuratEvening, CALENDAR, "open")).toBe(true);
    expect(sessionOpen(muhuratEvening, CALENDAR, "unknown")).toBe(false);
    expect(sessionOpen(ist(2026, 10, 3, 10, 0), CALENDAR, "unknown")).toBe(false);
    expect(sessionOpen(ist(2026, 10, 1, 9, 15), CALENDAR, "unknown")).toBe(true);
    expect(sessionOpen(ist(2026, 10, 1, 15, 30), CALENDAR, "unknown")).toBe(false);
  });

  it("is off by default and waits a full interval during an open session", () => {
    expect(autoLabel({ language: "en", auto: false, auto_minutes: 15, ...CALENDAR })).toBe("Auto-analyse off");
    expect(autoLabel({ language: "en", auto: true, auto_minutes: 15, ...CALENDAR })).toBe(
      "Auto every 15 minutes during open sessions",
    );
    const now = ist(2026, 10, 1, 10, 0);
    expect(autoDue(now, null, 15, true, CALENDAR)).toBe(false);
    expect(autoDue(now, now.getTime(), 15, false, CALENDAR)).toBe(false);
    expect(autoDue(now, now.getTime() - 14 * 60_000, 15, true, CALENDAR)).toBe(false);
    expect(autoDue(now, now.getTime() - 15 * 60_000, 15, true, CALENDAR)).toBe(true);
    const holiday = ist(2026, 10, 2, 10, 0);
    const budgetSunday = ist(2026, 2, 1, 10, 0);
    expect(autoDue(holiday, holiday.getTime() - 15 * 60_000, 15, true, CALENDAR)).toBe(false);
    expect(autoDue(budgetSunday, budgetSunday.getTime() - 15 * 60_000, 15, true, CALENDAR)).toBe(true);
    expect(autoDue(now, now.getTime() - 15 * 60_000, 15, true, CALENDAR, "closed")).toBe(false);
  });
});