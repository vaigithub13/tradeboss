import { describe, expect, it } from "vitest";

import { runJob, type LearnRead } from "./job";
import { defaultToggles, visibleLabels } from "./labels";
import {
  entryZone,
  equalLiquidity,
  liquiditySweeps,
  readLesson,
  textbookText,
  type Liquidity,
  type Textbook,
} from "./lesson";
import { readLevels } from "./levels";
import { stackedYs } from "./overlay";
import { SHADE, buildScene } from "./scene";
import { closedBars, readStructure, type LearnBar, type Structure, type Swing } from "./structure";
import { learnColors } from "./theme";

function at(day: number, hour: number, minute: number, high: number, low: number, close: number): LearnBar {
  return {
    time: Math.floor(Date.UTC(2026, 10, day, hour - 5, minute - 30) / 1000),
    open: close,
    high,
    low,
    close,
  };
}

function lessonOf(bars: LearnBar[], structure?: Structure, timeframe = "5m") {
  const levels = readLevels(bars);
  return readLesson({
    bars,
    structure: structure ?? readStructure(bars, { left: 1, right: 1 }),
    levels,
    timeframe,
    symbol: "NIFTY50",
  });
}

function swing(index: number, confirmIndex: number, price: number, kind: "high" | "low"): Swing {
  return { index, confirmIndex, time: index, price, kind, label: kind === "high" ? "H" : "L" };
}

const pdl: Liquidity = { id: "pdl", tag: "SSL", name: "PDL", price: 100, side: "low", knownAt: 0 };

describe("EQH and EQL tolerance", () => {
  const bars: LearnBar[] = Array.from({ length: 20 }, (_, i) => ({ time: i, open: 150, high: 200, low: 100, close: 150 }));

  it("clusters swings within 0.1 × ATR(14) and leaves a wider pair alone", () => {
    const close = equalLiquidity([swing(5, 14, 500, "high"), swing(8, 16, 505, "high")], bars);
    expect(close.map((level) => level.tag)).toEqual(["EQH"]);
    expect(close[0]?.price).toBe(502.5);
    const wide = equalLiquidity([swing(5, 14, 500, "high"), swing(8, 16, 520, "high")], bars);
    expect(wide).toEqual([]);
    const lows = equalLiquidity([swing(5, 14, 400, "low"), swing(8, 16, 403, "low")], bars);
    expect(lows.map((level) => level.tag)).toEqual(["EQL"]);
    const wideLows = equalLiquidity([swing(5, 14, 400, "low"), swing(8, 16, 430, "low")], bars);
    expect(wideLows).toEqual([]);
  });

  it("does not tag an equal high, or a sweep of it, before the confirming candle is closed", () => {
    const series: LearnBar[] = Array.from({ length: 18 }, (_, i) => at(26, 9, 15 + i, 150, 100, 125));
    series[14] = at(26, 9, 15 + 14, 200, 100, 125);
    series[16] = at(26, 9, 15 + 16, 201, 100, 125);
    const early = readStructure(series.slice(0, 17), { left: 1, right: 1 });
    expect(equalLiquidity(early.swings, series.slice(0, 17)).some((level) => level.tag === "EQH")).toBe(false);
    const confirmed = readStructure(series, { left: 1, right: 1 });
    const eqh = equalLiquidity(confirmed.swings, series).find((level) => level.tag === "EQH");
    expect(eqh).toBeTruthy();
    const beyond = at(26, 12, 0, 400, 100, 250);
    const back = at(26, 12, 5, 160, 100, 150);
    const waiting = lessonOf([...series, beyond]);
    expect(waiting.liquidity.some((level) => level.tag === "EQH")).toBe(true);
    expect(waiting.sweeps.some((sweep) => sweep.name === "EQH")).toBe(false);
    const swept = lessonOf([...series, beyond, back]);
    expect(swept.sweeps.map((sweep) => sweep.text)).toContain("Sweep of EQH");
  });
});

describe("sweep rule", () => {
  it("needs a close back on the same candle or the next, and not before that candle exists", () => {
    const same = { time: 1, open: 110, high: 110, low: 90, close: 105 };
    expect(liquiditySweeps([same], pdl)[0]).toMatchObject({ index: 0, text: "Sweep of PDL" });
    const beyond = { time: 1, open: 110, high: 110, low: 90, close: 80 };
    const back = { time: 2, open: 80, high: 110, low: 85, close: 105 };
    const stay = { time: 2, open: 80, high: 95, low: 70, close: 75 };
    expect(liquiditySweeps([beyond], pdl)).toEqual([]);
    expect(liquiditySweeps([beyond, back], pdl)[0]).toMatchObject({ index: 1, text: "Sweep of PDL" });
    expect(liquiditySweeps([beyond, stay], pdl)).toEqual([]);
  });

  it("does not call a close that stays outside the opening balance a manipulation", () => {
    const bars = [
      at(25, 9, 15, 110, 80, 100),
      at(26, 9, 15, 105, 100, 102),
      at(26, 10, 15, 109, 100, 106),
      at(26, 10, 20, 104, 70, 90),
    ];
    const read = lessonOf(bars);
    expect(read.sweeps.map((sweep) => sweep.text)).toContain("Sweep of PDL");
    expect(read.sessions.find((session) => session.day.endsWith("26"))?.manipulation).toBeNull();
  });
});

function sessionDay(day: number) {
  return [
    at(day, 9, 15, 105, 100, 102),
    at(day, 9, 30, 106, 100, 103),
    at(day, 9, 45, 107, 100, 104),
    at(day, 10, 0, 108, 100, 105),
    at(day, 10, 15, 109, 100, 106),
    at(day, 10, 20, 104, 90, 101),
    at(day, 10, 25, 103, 100, 102),
    at(day, 10, 30, 116, 108, 114),
  ];
}

function withStructure(bars: LearnBar[], events: { index: number; direction: "up" | "down" }[]): Structure {
  return {
    swings: [],
    events: events.map((event) => ({
      index: event.index,
      time: bars[event.index]!.time,
      kind: "choch" as const,
      direction: event.direction,
      price: 100,
      fromTime: bars[0]!.time,
      swingIndex: 0,
    })),
    trend: "Range",
  };
}

describe("AMD phases", () => {
  const prev = at(25, 9, 15, 110, 95, 105);
  const today = sessionDay(26);

  it("runs accumulation, then manipulation, then distribution, and stops the shade on a CHoCH", () => {
    const extra = [
      at(26, 10, 45, 108, 100, 106),
      at(26, 11, 0, 108, 100, 106),
      at(26, 15, 15, 108, 100, 104),
    ];
    const bars = [prev, ...today, ...extra];
    const structure = withStructure(bars, [{ index: 8, direction: "up" }, { index: 10, direction: "down" }]);
    const read = lessonOf(bars, structure);
    const session = read.sessions.find((item) => item.day.endsWith("26"));
    expect(session?.accumulation?.to).toBe(today[4]!.time);
    expect(session?.accumulation?.high).toBe(109);
    expect(session?.accumulation?.low).toBe(100);
    expect(session?.manipulation?.text).toBe("Sweep of Accum low");
    expect(session?.manipulation?.time).toBe(today[5]!.time);
    expect(session?.distribution?.direction).toBe("up");
    expect(session?.distribution?.from).toBe(today[7]!.time);
    expect(session?.distribution?.to).toBe(extra[1]!.time);
    expect(session?.note).toBeNull();
    expect(session!.accumulation!.to).toBeLessThan(session!.manipulation!.time);
    expect(session!.manipulation!.time).toBeLessThan(session!.distribution!.from);
    const scene = buildScene(jobRead(bars, structure), defaultToggles(), learnColors("light"), 12);
    expect(scene.boxes.find((box) => box.text === "Accumulation")?.color).toBe(SHADE.accumulation);
    expect(scene.boxes.find((box) => box.text === "Manipulation")?.color).toBe(SHADE.manipulation);
    expect(scene.boxes.find((box) => box.text === "Distribution")?.color).toBe(SHADE.up);
    expect(scene.marks.some((mark) => mark.text === "Sweep of Accum low")).toBe(true);
    const hidden = buildScene(jobRead(bars, structure), { ...defaultToggles(), amd: false, sweeps: false }, learnColors("light"), 12);
    expect(hidden.boxes.map((box) => box.text)).not.toContain("Accumulation");
    expect(hidden.marks.some((mark) => mark.text === "Sweep of Accum low")).toBe(false);
  });

  it("ends the opening balance early once the range exceeds the 20-day average", () => {
    const bars = [
      at(20, 9, 15, 110, 100, 105),
      at(21, 9, 15, 105, 100, 102),
      at(21, 9, 20, 112, 100, 108),
      at(21, 10, 0, 130, 100, 120),
    ];
    const session = lessonOf(bars).sessions.find((item) => item.day.endsWith("21"));
    expect(session?.accumulation?.to).toBe(bars[2]!.time);
    expect(session?.accumulation?.high).toBe(112);
  });

  it("labels No AMD today only after the session can no longer finish one", () => {
    const quiet = [
      prev,
      ...sessionDay(26).slice(0, 5),
      at(26, 10, 20, 104, 100, 102),
    ];
    expect(lessonOf(quiet).sessions.find((item) => item.day.endsWith("26"))?.note).toBeNull();
    const closed = [...quiet, at(26, 15, 15, 108, 100, 104)];
    const session = lessonOf(closed).sessions.find((item) => item.day.endsWith("26"));
    expect(session?.manipulation).toBeNull();
    expect(session?.note).toBe("No AMD today");
    const scene = buildScene(jobRead(closed), defaultToggles(), learnColors("light"), 12);
    expect(scene.amdNote).toBe("No AMD today");
    expect(buildScene(jobRead(closed), { ...defaultToggles(), amd: false }, learnColors("light"), 12).amdNote).toBeNull();
  });

  it("accepts a sweep of PDL that closes back inside the balance", () => {
    const bars = [
      at(25, 9, 15, 130, 102, 120),
      at(26, 9, 15, 105, 100, 102),
      at(26, 10, 15, 108, 100, 106),
      at(26, 10, 20, 106, 101, 105),
    ];
    expect(lessonOf(bars).sessions.find((item) => item.day.endsWith("26"))?.manipulation?.name).toBe("PDL");
  });

  it("does not build AMD on a daily chart", () => {
    const bars = [prev, ...today, at(26, 15, 15, 108, 100, 104)];
    expect(lessonOf(bars, undefined, "1D").sessions).toEqual([]);
  });
});

describe("textbook entry outcome", () => {
  const prev = at(25, 9, 15, 110, 95, 105);
  const today = sessionDay(26);

  function read(bars: LearnBar[]): Textbook | null {
    const structure = withStructure(bars, [{ index: 8, direction: "up" }]);
    return lessonOf(bars, structure).sessions.find((item) => item.day.endsWith("26"))?.textbook ?? null;
  }

  it("uses the first fair value gap, and the 50% only when that gap is absent", () => {
    const entry = read([prev, ...today]);
    expect(entry).toMatchObject({ priceLow: 104, priceHigh: 108, stop: 90, target: 109, outcome: null });
    expect(textbookText(entry!)).toBe("Textbook entry (learning)");
    const flat = [
      { time: 1, open: 100, high: 105, low: 90, close: 100 },
      { time: 2, open: 100, high: 108, low: 95, close: 104 },
      { time: 3, open: 104, high: 112, low: 100, close: 110 },
    ];
    expect(entryZone(flat, 0, 2, "up", 90)).toEqual({ low: 100, high: 100 });
    flat[2] = { time: 3, open: 110, high: 130, low: 120, close: 128 };
    expect(entryZone(flat, 0, 2, "up", 90)).toEqual({ low: 105, high: 120 });
  });

  it("reports the target, the stop, or neither only after that candle is closed", () => {
    const hit = at(26, 10, 40, 112, 100, 110);
    const after = at(26, 10, 45, 108, 100, 105);
    const stop = at(26, 10, 40, 120, 89, 100);
    const flat = at(26, 15, 15, 108, 100, 104);
    const throughChoch = [prev, ...today];
    expect(read(throughChoch)?.outcome).toBeNull();
    expect(read(closedBars([...throughChoch, hit], null))?.outcome).toBeNull();
    expect(read(closedBars([...throughChoch, hit, after], null))?.outcome).toBe("target hit");
    expect(textbookText(read(closedBars([...throughChoch, hit, after], null))!)).toContain("target hit");
    expect(read([...throughChoch, stop])?.outcome).toBe("stop hit");
    expect(read(throughChoch.concat(at(26, 10, 40, 108, 100, 105)))?.outcome).toBeNull();
    expect(read([...throughChoch, at(26, 10, 40, 108, 100, 105), flat])?.outcome).toBe("neither by 15:15");
    const shown = read(closedBars([...throughChoch, hit, after], null))!;
    const bars = closedBars([...throughChoch, hit, after], null);
    const scene = buildScene(jobRead(bars, withStructure(bars, [{ index: 8, direction: "up" }])), defaultToggles(), learnColors("light"), 12);
    expect(scene.boxes.some((box) => box.text === textbookText(shown))).toBe(true);
    const off = buildScene(jobRead(bars, withStructure(bars, [{ index: 8, direction: "up" }])), { ...defaultToggles(), textbook: false }, learnColors("light"), 12);
    expect(off.boxes.some((box) => box.text?.includes("Textbook"))).toBe(false);
  });
});

describe("labels and overlap", () => {
  it("keeps the last 12 swing labels and stacks two labels that share a spot", () => {
    const swings: Swing[] = Array.from({ length: 15 }, (_, index) => swing(index, index, 100 + index, "high"));
    const structure: Structure = {
      swings,
      events: [{ index: 1, time: 2, kind: "bos", direction: "up", price: 100, fromTime: 1, swingIndex: 0 }],
      trend: "Range",
    };
    const labels = visibleLabels(structure, [], defaultToggles(), null, 12);
    expect(labels.filter((label) => label.text === "H")).toHaveLength(12);
    expect(labels.some((label) => label.text === "BOS")).toBe(false);
    expect(stackedYs([
      { x: 10, y: 20, above: false, w: 30 },
      { x: 12, y: 20, above: false, w: 30 },
    ])).toEqual([20, 32]);
  });

  it("draws BOS as a short tag and drops a liquidity line when its toggle is off", () => {
    const structure: Structure = {
      swings: [swing(3, 4, 20, "high")],
      events: [{ index: 5, time: 6, kind: "bos", direction: "up", price: 20, fromTime: 2, swingIndex: 3 }],
      trend: "Uptrend",
    };
    const read: LearnRead = {
      structure,
      levels: [],
      lesson: {
        liquidity: [{ id: "bsl", tag: "BSL", name: "PDH", price: 20, side: "high", knownAt: 0 }],
        sweeps: [],
        sessions: [],
      },
      study: { sessions: [], boxes: [], lines: [], labels: [], events: [], banner: "", rounds: [] },
    };
    const colors = learnColors("light");
    const on = buildScene(read, defaultToggles(), colors, 12);
    expect(on.segments[0]?.short).toBe(true);
    expect(on.segments[0]?.text).toBe("BOS");
    expect(on.lines.some((line) => line.dotted && line.title === "BSL")).toBe(true);
    const off = buildScene(read, { ...defaultToggles(), liquidity: false, breaks: false }, colors, 12);
    expect(off.segments).toEqual([]);
    expect(off.lines.some((line) => line.dotted)).toBe(false);
  });
});

function jobRead(bars: LearnBar[], structure?: Structure): LearnRead {
  const done = runJob({ bars, left: 1, right: 1, rangeSwings: 4, timeframe: "5m", symbol: "NIFTY50" }, null);
  if (!structure) return done.read;
  return { ...done.read, structure, lesson: lessonOf(bars, structure) };
}
