import { describe, expect, it } from "vitest";

import type { Lesson } from "./lesson";
import {
  amdTarget,
  bookFromParts,
  costInR,
  entryAllowed,
  isRejection,
  readSignalBook,
  replayMatches,
  scoreSignal,
  verdictOf,
  whatWorks,
  zoneState,
  type WatchSignal,
} from "./signals";
import type { LearnBar, Structure } from "./structure";
import { istEpoch } from "./time";

function at(hour: number, minute: number, high: number, low: number, close: number, open = close): LearnBar {
  return { time: istEpoch(2026, 11, 26, hour, minute), open, high, low, close };
}

const structure = (events: Structure["events"] = []): Structure => ({ swings: [], events, trend: "Range" });
const lesson = (patch: Partial<Lesson> = {}): Lesson => ({ liquidity: [], sweeps: [], sessions: [], ...patch });

function signal(patch: Partial<WatchSignal> = {}): WatchSignal {
  return {
    id: "SWP-NIFTY50-1",
    symbol: "NIFTY50",
    setup: "SWP",
    side: "buy",
    time: istEpoch(2026, 11, 26, 10, 25),
    entry: 100,
    sl: 90,
    tp: 120,
    risk: 10,
    tpNote: "2R",
    why: "test",
    status: "open",
    exitTime: null,
    exit: null,
    r: null,
    liveR: null,
    slot: "midday",
    trend15: "flat",
    level: "other",
    expiry: false,
    costR: 0,
    ...patch,
  };
}

describe("Learn signals", () => {
  it("moves a plan from waiting to in the zone", () => {
    expect(zoneState([100, 110], 90)).toBe("waiting");
    expect(zoneState([100, 110], 105)).toBe("in_zone");
    const bars = [at(10, 0, 121, 119, 120), at(10, 5, 122, 118, 121)];
    const waiting = bookFromParts({
      bars,
      now: bars[1]!.time + 300,
      symbol: "NIFTY50",
      structure: structure(),
      gaps: [],
      lesson: lesson({
        liquidity: [{ id: "pdl", tag: "SSL", name: "PDL", price: 100, side: "low", knownAt: 0 }],
      }),
    });
    const plan = waiting.plans.find((item) => item.setup === "SWP");
    expect(plan?.state).toBe("waiting");
    expect(plan?.distance).toBeLessThan(0);
    const inside = [at(10, 0, 106, 99, 101), at(10, 5, 106, 99, 101)];
    const hot = bookFromParts({
      bars: inside,
      now: inside[1]!.time + 300,
      symbol: "NIFTY50",
      structure: structure([{ index: 0, time: inside[0]!.time, kind: "bos", direction: "up", price: 100, fromTime: inside[0]!.time, swingIndex: 0 }]),
      gaps: [{
        id: "gap", kind: "fvg_bull", t1: inside[0]!.time, t2: inside[1]!.time, p1: 102, p2: 100,
        color: "", border: "", label: "", labelColor: "", detail: "", confirm: inside[0]!.time, state: "open",
      }],
      lesson: lesson(),
    });
    expect(hot.plans.find((item) => item.setup === "FVG")?.state).toBe("in_zone");
  });

  it("keeps one SWP signal a day and cancels when the level breaks first", () => {
    const sweep = at(10, 0, 102, 98, 101);
    const mid = at(10, 5, 103, 100.5, 102);
    const choch = at(10, 10, 112, 101, 110);
    const later = at(10, 20, 112, 97, 99);
    const laterChoch = at(10, 25, 116, 100, 114);
    const bars = [sweep, mid, choch, later, laterChoch];
    const sweeps = [
      { index: 0, startIndex: 0, time: sweep.time, from: sweep.time, name: "PDL", text: "Sweep of PDL", price: 100, side: "low" as const, extreme: 98 },
      { index: 3, startIndex: 3, time: later.time, from: later.time, name: "EQL", text: "Sweep of EQL", price: 100, side: "low" as const, extreme: 97 },
    ];
    const events: Structure["events"] = [
      { index: 2, time: choch.time, kind: "choch", direction: "up", price: 105, fromTime: sweep.time, swingIndex: 0 },
      { index: 4, time: laterChoch.time, kind: "choch", direction: "up", price: 105, fromTime: later.time, swingIndex: 1 },
    ];
    const book = bookFromParts({
      bars, now: laterChoch.time + 300, symbol: "NIFTY50", structure: structure(events), gaps: [],
      lesson: lesson({ sweeps }),
    });
    expect(book.signals.filter((item) => item.setup === "SWP")).toHaveLength(1);
    expect(book.signals[0]?.why).toMatch(/PDL/);
    expect(book.signals[0]?.level).toBe("PDH/PDL");
    expect(book.signals[0]?.slot).toBe("midday");
    expect(book.done.some((item) => item.result === "not_counted")).toBe(true);

    const broken = at(10, 5, 101, 97, 99);
    const cancelled = bookFromParts({
      bars: [sweep, broken, choch],
      now: choch.time + 300,
      symbol: "NIFTY50",
      structure: structure([{ index: 2, time: choch.time, kind: "choch", direction: "up", price: 105, fromTime: sweep.time, swingIndex: 0 }]),
      gaps: [],
      lesson: lesson({ sweeps: [sweeps[0]!] }),
    });
    expect(cancelled.signals).toEqual([]);
    expect(cancelled.done.some((item) => item.result === "cancelled")).toBe(true);
  });

  it("does not enter after 15:00 and does not see a CHoCH before that candle closes", () => {
    expect(entryAllowed(istEpoch(2026, 11, 26, 15, 0))).toBe(true);
    expect(entryAllowed(istEpoch(2026, 11, 26, 15, 5))).toBe(false);
    const sweep = at(14, 40, 102, 98, 101);
    const late = at(15, 0, 112, 101, 110);
    const events: Structure["events"] = [
      { index: 1, time: late.time, kind: "choch", direction: "up", price: 105, fromTime: sweep.time, swingIndex: 0 },
    ];
    const parts = {
      bars: [sweep, late],
      symbol: "NIFTY50",
      structure: structure(events),
      gaps: [],
      lesson: lesson({
        sweeps: [{ index: 0, startIndex: 0, time: sweep.time, from: sweep.time, name: "PDH", text: "Sweep of PDH", price: 100, side: "low" as const, extreme: 98 }],
      }),
    };
    expect(bookFromParts({ ...parts, now: late.time + 300 }).signals).toEqual([]);
    const morning = at(10, 0, 102, 98, 101);
    const early = at(10, 10, 112, 101, 110);
    const morningSweep = { index: 0, startIndex: 0, time: morning.time, from: morning.time, name: "PDL", text: "Sweep of PDL", price: 100, side: "low" as const, extreme: 98 };
    const earlyEvent: Structure["events"] = [
      { index: 1, time: early.time, kind: "choch", direction: "up", price: 105, fromTime: morning.time, swingIndex: 0 },
    ];
    const hidden = bookFromParts({
      bars: [morning, early],
      symbol: "NIFTY50",
      structure: structure(earlyEvent),
      gaps: [],
      lesson: lesson({ sweeps: [morningSweep] }),
      now: early.time,
    });
    expect(hidden.signals).toEqual([]);
    const seen = bookFromParts({
      bars: [morning, early],
      symbol: "NIFTY50",
      structure: structure(earlyEvent),
      gaps: [],
      lesson: lesson({ sweeps: [morningSweep] }),
      now: early.time + 300,
    });
    expect(seen.signals).toHaveLength(1);
  });

  it("scores TP, SL, and expiry, and counts the stop when one candle hits both", () => {
    const open = signal();
    const next = at(10, 25, 130, 89, 125);
    expect(scoreSignal(open, [next], next.time + 300).status).toBe("SL");
    const target = at(10, 25, 121, 95, 120);
    expect(scoreSignal(open, [target], target.time + 300).status).toBe("TP");
    expect(scoreSignal(open, [target], target.time + 300).r).toBe(2);
    const nearerBar = at(10, 25, 111, 95, 110);
    const nearer = scoreSignal(signal({ tp: 110 }), [nearerBar], nearerBar.time + 300);
    expect(nearer.status).toBe("TP");
    expect(nearer.r).toBe(1);
    const flat = { ...at(15, 10, 106, 104, 105), time: istEpoch(2026, 11, 26, 15, 10) };
    const expired = scoreSignal(open, [flat], flat.time + 300);
    expect(expired.status).toBe("expired");
    expect(expired.r).toBe(0.5);
  });

  it("picks the nearer AMD target and names it", () => {
    expect(amdTarget("buy", 100, 10, [{ name: "PDH", price: 110 }]).note).toMatch(/PDH \(nearer than 2R\)/);
    expect(amdTarget("buy", 100, 10, [{ name: "PDH", price: 150 }]).note).toMatch(/2R \(nearer than PDH\)/);
    expect(amdTarget("sell", 100, 10, []).note).toMatch(/no opposite liquidity/);
  });

  it("greys a small sample and subtracts ₹110 as R", () => {
    expect(verdictOf(12, 60, 0.2)).toBe("edge, small sample");
    expect(verdictOf(80, 40, -0.1)).toBe("no edge");
    expect(verdictOf(60, 51, 0.05)).toBe("slight edge");
    expect(costInR(10, 65)).toBe(0.17);
    const rows = Array.from({ length: 12 }, (_, index) => signal({
      id: `s-${index}`,
      status: "TP",
      r: 1,
      costR: 0.17,
      setup: "FVG",
      level: "other",
    }));
    const cell = whatWorks(rows)[0];
    expect(cell?.n).toBe(12);
    expect(cell?.verdict).toMatch(/small sample/);
    expect(cell?.avgRNet).toBe(0.83);
  });

  it("rejects a bullish candle with a long lower wick and ignores a plain up close", () => {
    expect(isRejection({ time: 0, open: 10, high: 12, low: 4, close: 11 }, true)).toBe(true);
    expect(isRejection({ time: 0, open: 10, high: 12, low: 9.5, close: 11 }, true)).toBe(false);
  });

  it("matches a bar-by-bar replay to one pass, including a signal that appears only after its close", () => {
    const bars: LearnBar[] = [];
    for (let i = 0; i < 20; i += 1) {
      const minute = (15 + i * 5) % 60;
      const hour = 9 + Math.floor((15 + i * 5) / 60);
      bars.push(at(hour, minute, 110, 100, 105));
    }
    expect(replayMatches(bars, "NIFTY50", { left: 1, right: 1 })).toBe(true);
    const sweep = at(11, 0, 102, 98, 101);
    const choch = at(11, 10, 112, 101, 110);
    const after = at(11, 15, 121, 109, 120);
    const series = [...bars, sweep, choch, after];
    const end = readSignalBook(series, after.time + 300, "NIFTY50", { left: 1, right: 1 });
    const before = readSignalBook(series.slice(0, -1), choch.time, "NIFTY50", { left: 1, right: 1 });
    expect(before.signals.some((item) => item.time === choch.time + 300)).toBe(false);
    expect(JSON.stringify(readSignalBook(series, after.time + 300, "NIFTY50", { left: 1, right: 1 }).signals)).toBe(JSON.stringify(end.signals));
    let stepped = end;
    for (let i = 2; i < series.length; i += 1) {
      stepped = readSignalBook(series.slice(0, i + 1), series[i]!.time + 300, "NIFTY50", { left: 1, right: 1 });
    }
    expect(stepped.signals).toEqual(end.signals);
  });
});
