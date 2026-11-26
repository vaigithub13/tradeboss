import { describe, expect, it } from "vitest";

import { runJob } from "./job";
import { cprFrom, istParts, readLevels, roundLevels } from "./levels";
import { closedBars, readStructure, shownBars, type LearnBar } from "./structure";

function bar(time: number, high: number, low: number, close: number): LearnBar {
  return { time, open: close, high, low, close };
}

describe("pivots and HH/HL labels", () => {
  it("labels a later higher high and a later higher low, and only after the right bars exist", () => {
    const bars: LearnBar[] = [];
    for (let i = 0; i < 24; i += 1) bars.push(bar(1_000 + i * 60, 100, 90, 95));
    bars[3] = bar(bars[3]!.time, 120, 100, 110);
    bars[8] = bar(bars[8]!.time, 100, 70, 80);
    bars[14] = bar(bars[14]!.time, 140, 100, 130);
    bars[18] = bar(bars[18]!.time, 100, 78, 90);
    const early = readStructure(bars.slice(0, 6), { left: 3, right: 3 });
    expect(early.swings).toEqual([]);
    const full = readStructure(bars, { left: 3, right: 3 });
    const highs = full.swings.filter((swing) => swing.kind === "high").map((swing) => swing.label);
    const lows = full.swings.filter((swing) => swing.kind === "low").map((swing) => swing.label);
    expect(highs).toEqual(["H", "HH"]);
    expect(lows).toEqual(["L", "HL"]);
    expect(full.swings.every((swing) => swing.confirmIndex < bars.length)).toBe(true);
  });
});

describe("BOS and CHoCH", () => {
  const bars: LearnBar[] = [
    bar(1, 10, 5, 8),
    bar(2, 20, 6, 18),
    bar(3, 12, 7, 11),
    bar(4, 14, 4, 13),
    bar(5, 22, 8, 21),
    bar(6, 15, 3, 3),
  ];

  it("calls a close through the trend swing a BOS, and a close through the opposite swing a CHoCH", () => {
    const read = readStructure(bars, { left: 1, right: 1, rangeSwings: 4 });
    expect(read.swings.map((swing) => swing.label)).toEqual(["H", "L", "HH"]);
    expect(read.events.map((event) => `${event.kind} ${event.direction}`)).toEqual(["bos up", "choch down"]);
    expect(read.trend).toBe("Uptrend");
  });

  it("does not see a bar after the replay cursor, or the bar that is still forming", () => {
    expect(shownBars(bars, 5).map((item) => item.time)).toEqual([1, 2, 3, 4, 5]);
    const hidden = readStructure(closedBars(bars, 5), { left: 1, right: 1 });
    expect(hidden.events).toEqual([]);
    expect(hidden.swings.map((swing) => swing.label)).toEqual(["H"]);
    const live = readStructure(closedBars([...bars, bar(7, 16, 2, 4)], null), { left: 1, right: 1 });
    expect(live.events.map((event) => event.kind)).toEqual(["bos", "choch"]);
  });
});

describe("levels", () => {
  it("uses the market-view CPR and keeps the opening range inside 09:15–09:45", () => {
    const cpr = cprFrom(100, 80, 95);
    expect(cpr.pivot).toBeCloseTo(91.666, 2);
    expect(cpr.bc).toBeCloseTo(90, 2);
    expect(cpr.tc).toBeCloseTo(93.333, 2);
    const ist = (day: number, hour: number, minute: number) =>
      Math.floor(Date.UTC(2026, 10, day, hour - 5, minute - 30) / 1000);
    expect(istParts(ist(26, 9, 15)).minutes).toBe(9 * 60 + 15);
    const bars = [
      bar(ist(20, 15, 15), 200, 150, 180),
      bar(ist(25, 15, 15), 100, 80, 95),
      bar(ist(26, 9, 15), 110, 90, 100),
      bar(ist(26, 9, 30), 120, 88, 112),
      bar(ist(26, 9, 45), 115, 100, 108),
    ];
    const lines = readLevels(bars);
    expect(lines.find((line) => line.id === "pdh")?.price).toBe(100);
    expect(lines.find((line) => line.id === "pdl")?.price).toBe(80);
    expect(lines.find((line) => line.id === "prev")?.price).toBe(95);
    expect(lines.find((line) => line.id === "pivot")?.price).toBeCloseTo(91.67, 2);
    expect(lines.find((line) => line.id === "week-high")?.price).toBe(200);
    expect(lines.find((line) => line.id === "or-high")?.price).toBe(120);
    expect(lines.find((line) => line.id === "or-low")?.price).toBe(88);
    expect(roundLevels(24120, 24240, 24180)).toEqual([24200]);
  });
});

describe("incremental job", () => {
  it("appends one closed bar without relabelling the swings already confirmed", () => {
    const first = [bar(1, 10, 5, 8), bar(2, 20, 6, 18), bar(3, 12, 7, 11)];
    const step = runJob({ bars: first, left: 1, right: 1, rangeSwings: 4 }, null);
    expect(step.read.structure.swings.map((swing) => swing.label)).toEqual(["H"]);
    const more = runJob({ bars: [...first, bar(4, 14, 4, 13), bar(5, 22, 8, 21)], left: 1, right: 1, rangeSwings: 4 }, step.state);
    expect(more.read.structure.swings[0]?.label).toBe("H");
    expect(more.read.structure.events[0]?.kind).toBe("bos");
    expect(more.state.build.next).toBe(5);
  });
});
