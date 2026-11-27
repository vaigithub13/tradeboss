import { describe, expect, it } from "vitest";

import { sessionMarkdown } from "./exportSession";
import { scorePractice, tagAccuracy, type PracticeRow } from "./practice";

describe("Learn practice", () => {
  it("hides the score until six candles exist, then marks the direction and the level", () => {
    const upcoming = [1, 2, 3, 4, 5, 6].map((n) => ({ high: 100 + n, low: 99, close: 100 + n }));
    expect(scorePractice(100, upcoming.slice(0, 3), "up").ready).toBe(false);
    expect(scorePractice(100, upcoming, "up", 105)).toEqual({ ready: true, directionHit: true, levelHit: true });
    expect(scorePractice(100, upcoming, "down").directionHit).toBe(false);
  });

  it("keeps a running accuracy per tag", () => {
    const row = (tag: PracticeRow["tag"], hit: boolean): PracticeRow => ({
      id: tag + String(hit), day: "2026-11-27", symbol: "NIFTY50", direction: "up", reason: "higher low",
      tag, level: null, directionHit: hit, levelHit: null,
    });
    const tally = tagAccuracy([row("trend pullback", true), row("trend pullback", false), row("other", true)]);
    expect(tally.find((item) => item.tag === "trend pullback")).toEqual({ tag: "trend pullback", reads: 2, correct: 1 });
    expect(tally.find((item) => item.tag === "level break")?.reads).toBe(0);
  });

  it("writes the coach note with the banner, the read, and the practice score", () => {
    const text = sessionMarkdown({
      day: "2026-11-27",
      symbol: "NIFTY50",
      banners: ["Opening hour — accumulation forming"],
      amd: [],
      events: [{ title: "Republic Day", verified: true, source: "NSE/CMTR/71775" }],
      signals: [],
      notes: [],
      read: "Range day.",
      practice: [{
        id: "1", day: "2026-11-27", symbol: "NIFTY50", direction: "up", reason: "higher low",
        tag: "trend pullback", level: null, directionHit: true, levelHit: null,
      }],
    });
    expect(text).toMatch(/Now banner/);
    expect(text).toMatch(/Opening hour/);
    expect(text).toMatch(/Republic Day/);
    expect(text).toMatch(/Range day/);
    expect(text).toMatch(/trend pullback: 1\/1/);
  });
});
