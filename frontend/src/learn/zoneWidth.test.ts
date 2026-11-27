import { describe, expect, it } from "vitest";

import { planEdges } from "./chartLook";
import fixture from "./fixtures/nifty-5m-2026-11-27.json";
import { runJob } from "./job";
import { closedBars, type LearnBar } from "./structure";
import { istEpoch } from "./time";
import { clusterZones, zoneCap } from "./zones";

describe("key zone width", () => {
  it("27 Nov NIFTY 5m: no zone is wider than max(0.5 × ATR(14), 0.1% of price), and the plan pills use those zones", () => {
    const shown = fixture as LearnBar[];
    const bars = closedBars(shown, null);
    const now = shown.at(-1)!.time;
    const { study } = runJob({ bars, left: 3, right: 3, rangeSwings: 4, timeframe: "5m", symbol: "NIFTY50", now, m5: bars, signalNow: now, tfSeconds: 300 }, null).read;
    const cap = zoneCap(study.atr, study.last);
    expect(cap).toBeCloseTo(Math.max(0.5 * study.atr, 0.001 * study.last), 6);
    expect(study.zones.length).toBeGreaterThan(0);
    for (const zone of study.zones) expect(zone.high - zone.low).toBeLessThanOrEqual(cap + 1e-9);
    expect(study.zones.some((zone) => zone.low <= 25795 && zone.high >= 25702 && zone.high - zone.low > 90)).toBe(false);
    const pills = planEdges(study.zones, study.last);
    expect(pills.length).toBeGreaterThan(0);
    for (const pill of pills) {
      expect(pill.high - pill.low).toBeLessThanOrEqual(cap + 1e-9);
      expect(study.zones.some((zone) => zone.visible && zone.low === pill.low && zone.high === pill.high)).toBe(true);
    }
  });

  it("merges only within 0.35 × ATR and splits a cluster wider than the cap", () => {
    const time = istEpoch(2026, 11, 26, 10, 0);
    const bar: LearnBar = { time, open: 50, high: 51, low: 49, close: 50 };
    const levels = [100, 104, 108, 112, 116].map((price, i) => ({ name: ["PDH", "EQH", "Swing high", "OR high", "Round 100"][i]!, price, time }));
    const { zones } = clusterZones(levels, [bar], { atr: 20, span: 200 }, 50);
    expect(zones.map((zone) => [zone.low, zone.high])).toEqual([[100, 108], [112, 116]]);
    expect(zones[0]!.sources).toEqual(["PDH", "EQH", "Swing high"]);
    expect(zones[1]!.sources).toEqual(["OR high", "Round 100"]);
    expect(zones[0]!.strength).toBe(3);
    expect(zones[1]!.strength).toBe(2);
    expect(zones[0]!.label).toMatch(/●●●$/);
    expect(zones[1]!.label).toMatch(/ ●●$/);
    const apart = clusterZones([{ name: "PDH", price: 100, time }, { name: "EQH", price: 107, time }], [bar], { atr: 20, span: 200 }, 50);
    expect(apart.zones).toHaveLength(2);
    const joined = clusterZones([{ name: "PDH", price: 100, time }, { name: "EQH", price: 106.9, time }], [bar], { atr: 20, span: 200 }, 50);
    expect(joined.zones).toHaveLength(1);
  });

  it("27 Nov NIFTY 5m draws a support between 25,450 and 25,560, and the nearest zone on each side", () => {
    const shown = fixture as LearnBar[];
    const bars = closedBars(shown, null);
    const now = shown.at(-1)!.time;
    const { study } = runJob({ bars, left: 3, right: 3, rangeSwings: 4, timeframe: "5m", symbol: "NIFTY50", now, m5: bars, signalNow: now, tfSeconds: 300 }, null).read;
    const drawn = study.zones.filter((zone) => zone.visible);
    expect(drawn.some((zone) => zone.role === "support" && zone.high >= 25450 && zone.low <= 25560)).toBe(true);
    const gap = (zone: (typeof drawn)[number]) => (zone.role === "support" ? study.last - zone.high : zone.low - study.last);
    for (const role of ["support", "resistance"] as const) {
      const side = study.zones.filter((zone) => zone.role === role);
      const nearest = [...side].sort((a, b) => gap(a) - gap(b))[0]!;
      expect(nearest.visible).toBe(true);
    }
  });

  it("a split part keeps the parent's strength for the draw filter, and its dots still count its own sources", () => {
    const time = istEpoch(2026, 11, 26, 10, 0);
    const bar: LearnBar = { time, open: 50, high: 51, low: 49, close: 50 };
    const levels = [100, 104, 108, 112].map((price, i) => ({ name: ["PDH", "EQH", "Swing high", "OR high"][i]!, price, time }));
    const { zones } = clusterZones(levels, [bar], { atr: 20, span: 200 }, 50);
    const part = zones.find((zone) => zone.low === 112)!;
    expect(part.strength).toBe(1);
    expect(part.drawStrength).toBe(4);
    expect(part.label).toMatch(/ ●$/);
    expect(part.faint).toBe(false);
  });

  it("draws the nearest zone on each side even with one dot", () => {
    const time = istEpoch(2026, 11, 26, 10, 0);
    const bar: LearnBar = { time, open: 100, high: 101, low: 99, close: 100 };
    const { zones } = clusterZones([
      { name: "Round 50", price: 90, time },
      { name: "PDL", price: 60, time }, { name: "EQL", price: 60.5, time },
      { name: "Round 100", price: 110, time },
    ], [bar], { atr: 20, span: 20 }, 100);
    expect(zones.find((zone) => zone.low === 90)).toMatchObject({ visible: true, strength: 1 });
    expect(zones.find((zone) => zone.low === 90)!.label).toMatch(/ ●$/);
    expect(zones.find((zone) => zone.low === 110)).toMatchObject({ visible: true, strength: 1 });
  });

  it("27 Nov NIFTY 5m draws one EQH+50 support band covering 25,543–25,550 with 2 dots, inside the cap", () => {
    const shown = fixture as LearnBar[];
    const bars = closedBars(shown, null);
    const now = shown.at(-1)!.time;
    const { study } = runJob({ bars, left: 3, right: 3, rangeSwings: 4, timeframe: "5m", symbol: "NIFTY50", now, m5: bars, signalNow: now, tfSeconds: 300 }, null).read;
    const cap = zoneCap(study.atr, study.last);
    const bands = study.zones.filter((zone) => zone.visible && zone.role === "support" && Math.round(zone.low) <= 25543 && Math.round(zone.high) >= 25550);
    expect(bands).toHaveLength(1);
    expect(bands[0]!.label).toMatch(/· EQH\+50 ●●$/);
    expect(bands[0]!.strength).toBe(2);
    for (const zone of study.zones) expect(zone.high - zone.low).toBeLessThanOrEqual(cap + 1e-9);
  });
});
