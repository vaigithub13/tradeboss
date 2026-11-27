import { describe, expect, it } from "vitest";

import { candlesUrl } from "../api/client";
import { closedBars, readStructure, shownBars, type LearnBar } from "./structure";
import { istEpoch } from "./time";
import { backReplay, lastSessionOpen, learnReplayQuery, stepReplay } from "./replay";

const sessions = ["normal"] as const;

function bar(time: number, close: number): LearnBar {
  return { time, open: close, high: close + 2, low: close - 2, close };
}

describe("Learn replay", () => {
  it("asks for a page before the cursor and never sends limit with from/to", () => {
    const cursor = istEpoch(2026, 11, 26, 9, 45);
    const query = learnReplayQuery(
      { symbol: "NIFTY50", timeframe: "5m", sessions, limit: 400 },
      cursor,
    );
    expect(query.limit).toBe(400);
    expect(query.cursor).toBe(cursor);
    expect(query.to).toBeUndefined();
    expect(query.from).toBeUndefined();
    const url = candlesUrl(query);
    expect(url).toContain("limit=400");
    expect(url).toContain(`cursor=${cursor}`);
    expect(url).not.toContain("to=");
    expect(url).not.toContain("from=");

    const older = learnReplayQuery(
      { symbol: "NIFTY50", timeframe: "5m", sessions, limit: 400, before: cursor },
      cursor,
    );
    expect(older.before).toBe(cursor);
    expect(older.to).toBeUndefined();
    expect(candlesUrl(older)).not.toContain("to=");

    const newer = learnReplayQuery(
      { symbol: "NIFTY50", timeframe: "5m", sessions, limit: 1, after: cursor },
      cursor + 8 * 24 * 60 * 60,
    );
    expect(newer.after).toBe(cursor);
    expect(newer.limit).toBe(1);
    expect(newer.to).toBeUndefined();
  });

  it("opens replay at 09:15 of the last trading day", () => {
    expect(lastSessionOpen(istEpoch(2026, 11, 27, 19, 31))).toBe(istEpoch(2026, 11, 27, 9, 15));
    expect(lastSessionOpen(istEpoch(2026, 11, 28, 11, 0))).toBe(istEpoch(2026, 11, 27, 9, 15));
    expect(lastSessionOpen(istEpoch(2026, 11, 29, 12, 0))).toBe(istEpoch(2026, 11, 27, 9, 15));
    expect(lastSessionOpen(istEpoch(2026, 11, 25, 8, 0))).toBe(istEpoch(2026, 11, 23, 9, 15));
  });

  it("shows that day's candles up to 09:45, then steps and backs one candle", () => {
    const day = [15, 20, 25, 30, 35, 40, 45, 50].map((minute, index) => bar(istEpoch(2026, 11, 26, 9, minute), 100 + index));
    const later = bar(istEpoch(2026, 11, 26, 10, 0), 200);
    const cursor = istEpoch(2026, 11, 26, 9, 45);
    const loaded = day.filter((item) => item.time <= cursor);
    const shown = shownBars([...loaded, later], cursor);
    expect(shown.map((item) => item.time)).toEqual(loaded.map((item) => item.time));
    expect(shown[0]?.time).toBe(istEpoch(2026, 11, 26, 9, 15));
    expect(shown.at(-1)?.time).toBe(cursor);
    expect(shown.some((item) => item.time > cursor)).toBe(false);

    expect(stepReplay(loaded, cursor)).toEqual({ kind: "newer", after: cursor });
    const withNext = [...loaded, day[7]!];
    const stepped = stepReplay(withNext, cursor);
    expect(stepped).toEqual({ kind: "move", cursor: day[7]!.time });
    if (stepped.kind !== "move") return;
    const afterStep = shownBars(withNext, stepped.cursor);
    expect(afterStep).toHaveLength(shown.length + 1);
    expect(afterStep.at(-1)?.time).toBe(istEpoch(2026, 11, 26, 9, 50));
    expect(afterStep.some((item) => item.time > stepped.cursor)).toBe(false);

    const backed = backReplay(withNext, stepped.cursor);
    expect(backed).toEqual({ kind: "move", cursor });
    if (backed.kind !== "move") return;
    const afterBack = shownBars(withNext, backed.cursor);
    expect(afterBack).toHaveLength(afterStep.length - 1);
    expect(afterBack.some((item) => item.time > cursor)).toBe(false);

    const closed = closedBars(afterBack, cursor);
    expect(closed.every((item) => item.time < cursor)).toBe(true);
    const structure = readStructure(closed);
    const closedTimes = new Set(closed.map((item) => item.time));
    for (const swing of structure.swings) expect(closedTimes.has(swing.time)).toBe(true);
    for (const event of structure.events) {
      expect(event.time).toBeLessThan(cursor);
      expect(closedTimes.has(event.time)).toBe(true);
    }
  });
});
