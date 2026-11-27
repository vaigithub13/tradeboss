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

  it("merges only within 0.25 × ATR and splits a cluster wider than the cap", () => {
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
    const apart = clusterZones([{ name: "PDH", price: 100, time }, { name: "EQH", price: 106, time }], [bar], { atr: 20, span: 200 }, 50);
    expect(apart.zones).toHaveLength(2);
  });
});
