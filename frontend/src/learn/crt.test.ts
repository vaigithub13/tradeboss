import { describe, expect, it } from "vitest";

import { crtPairs, crtSlot, crtCandles, nextSession, type CrtPair } from "./crt";
import { crtSignals, readSignalBook, scoreSignal } from "./signals";
import { waitLine } from "./SignalsPanel";
import type { BreakEvent, LearnBar } from "./structure";
import { istEpoch } from "./time";

const M5 = 300;

function t(day: number, hour: number, minute: number): number {
  return istEpoch(2026, 11, day, hour, minute);
}

/** One 5m bar every 5 minutes from `from` up to (not including) `to`. Each bar uses `shape(i)`. */
function run(day: number, from: [number, number], to: [number, number], shape: (i: number) => [number, number, number, number]): LearnBar[] {
  const out: LearnBar[] = [];
  for (let at = t(day, from[0], from[1]), i = 0; at < t(day, to[0], to[1]); at += M5, i += 1) {
    const [open, high, low, close] = shape(i);
    out.push({ time: at, open, high, low, close });
  }
  return out;
}

const flat = (price: number, wide = 2) => () => [price, price + wide, price - wide, price] as [number, number, number, number];

/** Candle 1 09:15–10:30 ranges lo–hi. Candle 2 10:30–11:45 is given. Then 11:45–15:30 is given. */
function day26(candle2: LearnBar[], after: LearnBar[] = [], hi = 140, lo = 100): LearnBar[] {
  const one = run(26, [9, 15], [10, 30], (i) => (i === 3 ? [120, hi, 119, 121] : i === 9 ? [120, 121, lo, 119] : [120, 122, 118, 120]));
  return [...one, ...candle2, ...after];
}

function sweepLow(close = 110): LearnBar[] {
  return run(26, [10, 30], [11, 45], (i) => (i === 4 ? [104, 106, 95, 103] : i === 14 ? [108, 111, 107, close] : [108, 110, 104, 108]));
}

function event(time: number, index: number, direction: "up" | "down", kind: "bos" | "choch" = "choch"): BreakEvent {
  return { index, time, kind, direction, price: 0, fromTime: time, swingIndex: 0 };
}

function pairAt(bars: LearnBar[], now: number, c2: number): CrtPair | undefined {
  return crtPairs(bars, now, "75m").find((pair) => pair.c2.t === c2);
}

describe("CRT 75-minute candles", () => {
  it("anchors 75m slots at 09:15, 10:30, 11:45, 13:00 and 14:15 IST", () => {
    expect(crtSlot(t(26, 9, 15))).toBe(t(26, 9, 15));
    expect(crtSlot(t(26, 10, 25))).toBe(t(26, 9, 15));
    expect(crtSlot(t(26, 10, 30))).toBe(t(26, 10, 30));
    expect(crtSlot(t(26, 11, 45))).toBe(t(26, 11, 45));
    expect(crtSlot(t(26, 13, 0))).toBe(t(26, 13, 0));
    expect(crtSlot(t(26, 14, 20))).toBe(t(26, 14, 15));
    expect(crtSlot(t(26, 15, 25))).toBe(t(26, 14, 15));
    expect(crtSlot(t(26, 15, 30))).toBeNull();
    expect(crtSlot(t(26, 9, 10))).toBeNull();
  });

  it("builds the five candles of a session from closed 5m bars", () => {
    const bars = run(26, [9, 15], [15, 30], (i) => [100 + i, 101 + i, 99 + i, 100 + i]);
    const candles = crtCandles(bars, t(26, 15, 30), "75m");
    expect(candles.map((c) => c.t)).toEqual([t(26, 9, 15), t(26, 10, 30), t(26, 11, 45), t(26, 13, 0), t(26, 14, 15)]);
    expect(candles[0]).toMatchObject({ open: 100, high: 115, low: 99, close: 114, end: t(26, 10, 30), complete: true });
    const mid = crtCandles(bars, t(26, 11, 0), "75m");
    expect(mid.at(-1)).toMatchObject({ t: t(26, 10, 30), complete: false, close: 120 });
  });

  it("builds daily candles that complete at 15:30", () => {
    const bars = run(26, [9, 15], [15, 30], flat(100));
    expect(crtCandles(bars, t(26, 15, 25), "D1")[0]!.complete).toBe(false);
    expect(crtCandles(bars, t(26, 15, 30), "D1")[0]).toMatchObject({ t: t(26, 9, 15), end: t(26, 15, 30), complete: true, high: 102, low: 98 });
  });
});

describe("CRT rule", () => {
  it("confirms CRT when candle 2 sweeps one side and closes back inside", () => {
    const bars = day26(sweepLow());
    const pair = pairAt(bars, t(26, 11, 45), t(26, 10, 30))!;
    expect(pair).toMatchObject({ status: "crt", swept: "low", bias: "up", hi: 140, lo: 100, mid: 120, ext: 95, target: 140, close2: 110 });
    expect(pair.sweepAt).toBe(t(26, 10, 50) + M5);
  });

  it("confirms a high sweep as a sell bias with the low as target", () => {
    const two = run(26, [10, 30], [11, 45], (i) => (i === 2 ? [130, 146, 129, 132] : [130, 132, 128, 130]));
    const pair = pairAt(day26(two), t(26, 11, 45), t(26, 10, 30))!;
    expect(pair).toMatchObject({ status: "crt", swept: "high", bias: "down", ext: 146, target: 100 });
  });

  it("cancels when candle 2 closes outside the range (breakout)", () => {
    const pair = pairAt(day26(sweepLow(98)), t(26, 11, 45), t(26, 10, 30))!;
    expect(pair.status).toBe("breakout");
    expect(pair.bias).toBeNull();
  });

  it("cancels when candle 2 takes both sides", () => {
    const two = run(26, [10, 30], [11, 45], (i) => (i === 2 ? [130, 146, 129, 132] : i === 8 ? [110, 111, 96, 108] : [120, 122, 118, 120]));
    const pair = pairAt(day26(two), t(26, 11, 45), t(26, 10, 30))!;
    expect(pair.status).toBe("outside");
    expect(pair.swept).toBe("both");
  });

  it("does not look ahead: candle 2 is only forming before its 75m close", () => {
    const bars = day26(sweepLow());
    const before = pairAt(bars, t(26, 11, 40), t(26, 10, 30))!;
    expect(before.status).toBe("forming");
    expect(before.swept).toBe("low");
    expect(before.close2).toBe(108);
    const early = pairAt(bars, t(26, 10, 50), t(26, 10, 30))!;
    expect(early.status).toBe("pending");
    expect(early.swept).toBeNull();
  });

  it("marks the target reached during candle 3 only", () => {
    const after = run(26, [11, 45], [13, 0], (i) => (i === 6 ? [130, 141, 129, 139] : [125, 127, 123, 125]));
    const bars = day26(sweepLow(), after);
    expect(pairAt(bars, t(26, 12, 10), t(26, 10, 30))!.hitAt).toBeNull();
    expect(pairAt(bars, t(26, 13, 0), t(26, 10, 30))!.hitAt).toBe(t(26, 12, 15) + M5);
  });

  it("finds the next trading session for a daily CRT", () => {
    expect(nextSession("2026-11-27")).toBe("2026-11-30");
    expect(nextSession("2026-11-26")).toBe("2026-11-27");
  });
});

describe("CRT watch-only signals", () => {
  const after = run(26, [11, 45], [15, 30], (i) => (i === 3 ? [110, 113, 109, 112] : i === 30 ? [140, 151, 139, 149] : [111, 113, 109, 111]));
  const bars = day26(sweepLow(), after, 150);
  const breakIndex = bars.findIndex((bar) => bar.time === t(26, 12, 0));

  it("enters on the first 5m BOS/CHoCH toward the target after the 75m close, SL below the sweep low", () => {
    const events = [event(t(26, 11, 0), 20, "up"), event(t(26, 12, 0), breakIndex, "up", "bos")];
    const { signals } = crtSignals(bars, events, t(26, 15, 30), "NIFTY50");
    expect(signals).toHaveLength(1);
    const signal = signals[0]!;
    expect(signal).toMatchObject({ setup: "CRT75", side: "buy", entry: 112, tp: 150, time: t(26, 12, 5) });
    expect(signal.sl).toBeLessThan(95);
    expect(signal.sl).toBeGreaterThan(90);
    expect(signal.tp).toBeGreaterThan(signal.entry);
    expect(signal.why).toContain("75m CRT");
  });

  it("puts a SELL stop above the sweep high", () => {
    const two = run(26, [10, 30], [11, 45], (i) => (i === 2 ? [130, 146, 129, 132] : [131, 133, 129, 131]));
    const rest = run(26, [11, 45], [15, 30], (i) => (i === 2 ? [131, 132, 128, 128] : [129, 131, 127, 129]));
    const sellBars = day26(two, rest, 140, 80);
    const at = sellBars.findIndex((bar) => bar.time === t(26, 11, 55));
    const { signals } = crtSignals(sellBars, [event(t(26, 11, 55), at, "down")], t(26, 15, 30), "NIFTY50");
    expect(signals).toHaveLength(1);
    expect(signals[0]!.side).toBe("sell");
    expect(signals[0]!.sl).toBeGreaterThan(146);
    expect(signals[0]!.tp).toBe(80);
  });

  it("ignores a break inside candle 2 and a break the wrong way", () => {
    const wrong = [event(t(26, 11, 0), 20, "up"), event(t(26, 12, 0), breakIndex, "down")];
    expect(crtSignals(bars, wrong, t(26, 15, 30), "NIFTY50").signals).toHaveLength(0);
  });

  it("is absent until the entry candle has closed", () => {
    const events = [event(t(26, 12, 0), breakIndex, "up")];
    expect(crtSignals(bars, events, t(26, 12, 0), "NIFTY50").signals).toHaveLength(0);
    expect(crtSignals(bars, events, t(26, 12, 5), "NIFTY50").signals).toHaveLength(1);
  });

  it("skips a CRT whose target is under 2R from the entry", () => {
    const near = bars.map((bar) => (bar.time === t(26, 12, 0) ? { ...bar, high: 131, close: 130 } : bar));
    const { signals, done } = crtSignals(near, [event(t(26, 12, 0), breakIndex, "up")], t(26, 15, 30), "NIFTY50");
    expect(signals).toHaveLength(0);
    expect(done.some((item) => /under 2R/.test(item.text))).toBe(true);
  });

  it("cancels when a close goes back beyond the sweep before the entry", () => {
    const broken = bars.map((bar) => (bar.time === t(26, 11, 50) ? { ...bar, low: 93, close: 94 } : bar));
    const { signals, done } = crtSignals(broken, [event(t(26, 12, 0), breakIndex, "up")], t(26, 15, 30), "NIFTY50");
    expect(signals).toHaveLength(0);
    expect(done.some((item) => item.result === "cancelled")).toBe(true);
  });

  it("keeps one 75m CRT signal per day", () => {
    const three = run(26, [11, 45], [13, 0], (i) => (i === 3 ? [110, 113, 109, 112] : i === 14 ? [111, 112, 105, 108] : [111, 113, 109, 111]));
    const four = run(26, [13, 0], [15, 30], (i) => (i === 2 ? [109, 110, 103, 108] : i === 16 ? [109, 112, 108, 111] : [109, 111, 107, 109]));
    const twice = day26(sweepLow(), [...three, ...four], 150);
    const first = twice.findIndex((bar) => bar.time === t(26, 12, 0));
    const second = twice.findIndex((bar) => bar.time === t(26, 14, 20));
    expect(crtPairs(twice, t(26, 15, 30), "75m").find((pair) => pair.c2.t === t(26, 13, 0))!.status).toBe("crt");
    const { signals, done } = crtSignals(twice, [event(t(26, 12, 0), first, "up"), event(t(26, 14, 20), second, "up")], t(26, 15, 30), "NIFTY50");
    expect(signals.filter((s) => s.setup === "CRT75")).toHaveLength(1);
    expect(done.some((item) => item.result === "not_counted")).toBe(true);
  });

  it("scores a CRT signal at the range target with the real R", () => {
    const { signals } = crtSignals(bars, [event(t(26, 12, 0), breakIndex, "up")], t(26, 15, 30), "NIFTY50");
    const scored = scoreSignal(signals[0]!, bars, t(26, 15, 30));
    expect(scored.status).toBe("TP");
    expect(scored.exit).toBe(150);
    expect(scored.r).toBeCloseTo((150 - 112) / signals[0]!.risk, 1);
  });

  it("reads a daily CRT at 15:30 and gives the next session's entry", () => {
    const d1 = run(25, [9, 15], [15, 30], (i) => (i === 10 ? [120, 150, 119, 121] : i === 40 ? [120, 121, 100, 119] : [120, 122, 118, 120]));
    const d2 = run(26, [9, 15], [15, 30], (i) => (i === 20 ? [104, 106, 95, 103] : [108, 110, 104, 108]));
    const d3 = run(27, [9, 15], [15, 30], (i) => (i === 6 ? [110, 113, 109, 112] : [111, 113, 109, 111]));
    const all = [...d1, ...d2, ...d3];
    expect(crtPairs(all, t(26, 15, 25), "D1").at(-1)!.status).toBe("forming");
    const pair = crtPairs(all, t(26, 15, 30), "D1").at(-1)!;
    expect(pair).toMatchObject({ status: "crt", bias: "up", target: 150, ext: 95 });
    const sameDay = all.findIndex((bar) => bar.time === t(26, 15, 0));
    const next = all.findIndex((bar) => bar.time === t(27, 9, 45));
    const events = [event(t(26, 15, 0), sameDay, "up"), event(t(27, 9, 45), next, "up")];
    const { signals } = crtSignals(all, events, t(27, 15, 30), "NIFTY50");
    const daily = signals.filter((s) => s.setup === "CRTD");
    expect(daily).toHaveLength(1);
    expect(daily[0]).toMatchObject({ side: "buy", entry: 112, tp: 150, time: t(27, 9, 50) });
    expect(daily[0]!.sl).toBeLessThan(95);
  });
});

describe("CRT plan card", () => {
  it("counts down to the 75m close: must close above the swept low for a CRT BUY", () => {
    const bars = day26(sweepLow());
    const now = t(26, 11, 30);
    const book = readSignalBook(bars, now, "NIFTY50");
    const plan = book.plans.find((item) => item.setup === "CRT75")!;
    expect(plan.side).toBe("buy");
    expect(plan.waitUntil).toBe(t(26, 11, 45));
    expect(plan.waitText).toBe("⏰ 75m closes 11:45 IST — must close above 100 for a CRT BUY");
    expect(plan.sl).toBeLessThan(95);
    expect(plan.tp).toBe(140);
    expect(waitLine(plan, now + 15, false)).toBe("⏰ 75m closes 11:45 IST — must close above 100 for a CRT BUY — in 14:45");
    expect(waitLine(plan, now, true)).toContain("replay, clock paused");
  });

  it("matches one pass when the cursor walks forward bar by bar (no look-ahead)", () => {
    const after = run(26, [11, 45], [15, 30], (i) => (i === 3 ? [110, 113, 109, 112] : i === 30 ? [140, 151, 139, 149] : [111, 113, 109, 111]));
    const bars = day26(sweepLow(), after, 150);
    const at = bars.findIndex((bar) => bar.time === t(26, 12, 0));
    const events = [event(t(26, 12, 0), at, "up")];
    const full = crtSignals(bars, events, t(26, 15, 30), "NIFTY50").signals;
    let stepped = crtSignals([], [], 0, "NIFTY50").signals;
    for (let i = 0; i < bars.length; i += 1) {
      const now = bars[i]!.time + M5;
      const seen = bars.slice(0, i + 1);
      stepped = crtSignals(seen, events.filter((item) => item.index <= i), now, "NIFTY50").signals;
      if (now < t(26, 12, 5)) expect(stepped).toHaveLength(0);
    }
    expect(stepped).toEqual(full);
    expect(full).toHaveLength(1);
  });
});
