import { describe, expect, it } from "vitest";

import { EXPLAIN_CARDS, explainCard } from "./explainCards";
import { pushLiveBar } from "./liveBars";
import { buildStudy, bannerText, candlePatterns, daySessions, fairValueGaps, sessionName, sweepFailed } from "./study";
import { closedBars, shownBars, type LearnBar, type Structure } from "./structure";
import type { AmdSession } from "./lesson";
import { istEpoch } from "./time";

function at(hour: number, minute: number): number {
  return istEpoch(2026, 11, 26, hour, minute);
}

function bar(hour: number, minute: number, high: number, low: number, close: number, open = close): LearnBar {
  return { time: at(hour, minute), open, high, low, close };
}

describe("Learn study", () => {
  it("names the Nifty sessions in IST", () => {
    expect(sessionName(at(9, 15))).toBe("Opening hour");
    expect(sessionName(at(10, 14))).toBe("Opening hour");
    expect(sessionName(at(10, 15))).toBe("Midday");
    expect(sessionName(at(13, 29))).toBe("Midday");
    expect(sessionName(at(13, 30))).toBe("Closing");
    expect(sessionName(at(15, 29))).toBe("Closing");
    expect(sessionName(at(15, 30))).toBe("After the close");
    const sessions = daySessions("2026-11-26");
    expect(sessions.map((session) => session.name)).toEqual(["Opening hour", "Midday", "Closing"]);
    expect(sessionName(sessions[0]!.t1)).toBe("Opening hour");
    expect(sessionName(sessions[0]!.t2)).toBe("Midday");
    expect(sessionName(sessions[2]!.t2)).toBe("After the close");
  });

  it("writes one plain line for the opening hour and for a failed sweep", () => {
    const forming: AmdSession = {
      day: "2026-11-26", accumulation: null, manipulation: null, distribution: null, note: null, textbook: null,
    };
    expect(bannerText(forming, at(9, 20), false)).toBe("Opening hour — accumulation forming");
    const failed: AmdSession = {
      day: "2026-11-26",
      accumulation: { from: at(9, 15), to: at(10, 15), high: 110, low: 100, endIndex: 1 },
      manipulation: {
        index: 2, startIndex: 2, time: at(10, 20), from: at(10, 20),
        name: "PDL", text: "Sweep of PDL", price: 99, side: "low", extreme: 97,
      },
      distribution: null,
      note: null,
      textbook: null,
    };
    const bars = [bar(9, 15, 110, 100, 105), bar(10, 15, 110, 100, 105), bar(10, 20, 100, 97, 100), bar(10, 25, 98, 96, 96)];
    expect(sweepFailed(failed, bars)).toBe(true);
    expect(bannerText(failed, at(10, 25), true)).toBe("Sweep of PDL failed — no AMD read; stay patient");
    expect(sweepFailed({ ...failed, distribution: { from: at(10, 30), to: at(11, 0), direction: "up" } }, bars)).toBe(false);
  });

  it("gives every label type an explain card with the four questions", () => {
    for (const kind of Object.keys(EXPLAIN_CARDS)) {
      const card = explainCard(kind);
      expect(card.what.length).toBeGreaterThan(10);
      expect(card.why.length).toBeGreaterThan(10);
      expect(card.next.length).toBeGreaterThan(10);
      expect(card.what.toLowerCase()).not.toContain("gold");
      expect(card.what.toLowerCase()).not.toContain("london");
    }
    expect(explainCard("pdh").title).toMatch(/PDH/);
    expect(explainCard("or_h").what).toMatch(/09:15/);
  });

  it("lists events newest first", () => {
    const bars = [bar(9, 15, 101, 99, 100), bar(10, 30, 110, 98, 109)];
    const structure: Structure = {
      swings: [],
      trend: "Range",
      events: [
        { index: 0, time: bars[0]!.time, kind: "bos", direction: "up", price: 101, fromTime: bars[0]!.time, swingIndex: 0 },
        { index: 1, time: bars[1]!.time, kind: "choch", direction: "down", price: 99, fromTime: bars[1]!.time, swingIndex: 0 },
      ],
    };
    const study = buildStudy({
      bars, structure, lesson: { liquidity: [], sweeps: [], sessions: [] },
      now: bars[1]!.time, low: 98, high: 110, last: 109,
    });
    expect(study.events[0]?.text).toMatch(/CHoCH/);
    expect(study.events[1]?.text).toMatch(/BOS/);
    const times = study.events.map((event) => event.time);
    expect(times).toEqual([...times].sort((a, b) => b - a));
  });

  it("does not mark a gap, a pattern, or a later bar until that candle has closed", () => {
    const first = bar(9, 15, 100, 90, 95, 92);
    const second = bar(9, 20, 120, 110, 118, 100);
    const third = bar(9, 25, 130, 112, 125, 118);
    expect(fairValueGaps([first, second])).toEqual([]);
    const gaps = fairValueGaps([first, second, third]);
    expect(gaps).toHaveLength(1);
    expect(gaps[0]?.confirm).toBe(third.time);
    expect(gaps[0]?.kind).toBe("fvg_bull");
    const engulf = bar(9, 30, 110, 90, 108, 94);
    const prev = bar(9, 25, 101, 92, 95, 100);
    expect(candlePatterns([prev], [], [])).toEqual([]);
    expect(candlePatterns([prev, engulf], [], []).some((label) => label.kind === "engulf_bull")).toBe(true);
    const cursor = second.time;
    const visible = shownBars([first, second, third], cursor);
    expect(fairValueGaps(closedBars(visible, cursor))).toEqual([]);
    expect(visible.every((item) => item.time <= cursor)).toBe(true);
  });

  it("updates the forming candle before any study can see it", () => {
    const first = bar(9, 15, 100, 90, 95, 92);
    const second = bar(9, 20, 120, 110, 118, 100);
    const forming = bar(9, 25, 130, 112, 125, 118);
    const order: string[] = [];
    const next = pushLiveBar([first, second], [forming], () => order.push("update"), (item) => item.time);
    order.push("study");
    expect(order).toEqual(["update", "study"]);
    expect(fairValueGaps(closedBars(next, null))).toEqual([]);
    expect(fairValueGaps(next)).toHaveLength(1);
    expect(closedBars(next, null).some((item) => item.time === forming.time)).toBe(false);
  });
});
