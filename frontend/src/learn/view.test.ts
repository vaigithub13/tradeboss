import { describe, expect, it } from "vitest";

import { fairValueGaps } from "./study";
import { viewFor } from "./presets";
import { mergeTags } from "./tags";
import type { LearnBar } from "./structure";
import { istEpoch } from "./time";
import { clusterZones } from "./zones";

function at(day: number, hour: number, minute: number): number {
  return istEpoch(2026, 11, day, hour, minute);
}

describe("Learn view", () => {
  it("rejects an overnight gap and a zone larger than 1.5 ATR", () => {
    const overnight: LearnBar[] = [
      { time: at(26, 15, 25), open: 100, high: 101, low: 99, close: 100 },
      { time: at(27, 9, 15), open: 400, high: 401, low: 399, close: 400 },
      { time: at(27, 9, 20), open: 450, high: 452, low: 448, close: 450 },
    ];
    expect(fairValueGaps(overnight)).toEqual([]);

    const wide: LearnBar[] = [];
    for (let i = 0; i < 20; i += 1) {
      wide.push({ time: at(26, 9, 15) + i * 300, open: 100, high: 101, low: 100, close: 100 });
    }
    wide[17] = { time: wide[17]!.time, open: 100, high: 101, low: 100, close: 100 };
    wide[18] = { time: wide[18]!.time, open: 110, high: 112, low: 108, close: 111 };
    wide[19] = { time: wide[19]!.time, open: 140, high: 142, low: 131, close: 140 };
    expect(fairValueGaps(wide)).toEqual([]);
  });

  it("clusters nearby levels, flips a broken zone, and draws two on each side", () => {
    const bars: LearnBar[] = [0, 1, 2, 3].map((step) => ({
      time: at(26, 10, step * 5), open: 110, high: 112, low: 108, close: 110,
    }));
    bars.push({ time: at(26, 10, 25), open: 100, high: 101, low: 99, close: 99 });
    const { zones, flips } = clusterZones([
      { name: "PDL", price: 100, time: bars[0]!.time },
      { name: "EQL", price: 100.2, time: bars[0]!.time },
      { name: "Swing low", price: 100.4, time: bars[0]!.time },
      { name: "PDH", price: 120, time: bars[0]!.time },
      { name: "Round 100", price: 130, time: bars[0]!.time },
    ], bars, 4, 99);
    const support = zones.find((zone) => zone.sources.includes("PDL"));
    expect(support?.sources).toEqual(["PDL", "EQL", "Swing low"]);
    expect(support?.flipped).toBe(true);
    expect(support?.role).toBe("resistance");
    expect(support?.label.startsWith("R ")).toBe(true);
    expect(support?.detail).toMatch(/Sources: PDL, EQL, Swing low/);
    expect(support?.detail).toMatch(/Touches:/);
    expect(flips.some((flip) => flip.text === "Support flipped to resistance")).toBe(true);
    expect(zones.filter((zone) => zone.visible)).toHaveLength(2);
    expect(zones.filter((zone) => !zone.visible).every((zone) => zone.detail.length > 0)).toBe(true);
  });

  it("merges axis tags within 0.15 ATR and keeps only six on the axis", () => {
    const merged = mergeTags([
      { kind: "pd", text: "PDL", price: 100, detail: "Previous day low" },
      { kind: "eq", text: "EQL", price: 100.4, detail: "Equal lows" },
    ], 10, 100);
    expect(merged.shown).toHaveLength(1);
    expect(merged.shown[0]?.text).toBe("PDL · EQL");
    expect(merged.shown[0]?.detail).toMatch(/Previous day low/);
    expect(merged.shown[0]?.detail).toMatch(/Equal lows/);

    const many = Array.from({ length: 8 }, (_, index) => ({
      kind: "pd" as const,
      text: `L${index}`,
      price: index * 10,
      detail: `Level ${index} stays in the list`,
    }));
    const limited = mergeTags(many, 10, 35);
    expect(limited.shown).toHaveLength(6);
    expect(limited.rest).toHaveLength(2);
    expect(limited.rest.every((tag) => tag.detail.length > 0)).toBe(true);
  });

  it("offers Levels, AMD, ICT, and Full without dropping a layer the others use", () => {
    const levels = viewFor("levels");
    expect(levels.layers.zones).toBe(true);
    expect(levels.layers.swing).toBe(true);
    expect(levels.tags.pd).toBe(true);
    expect(levels.layers.fvg).toBe(false);
    const amd = viewFor("amd");
    expect(amd.layers.amd).toBe(true);
    expect(amd.layers.zones).toBe(false);
    const ict = viewFor("ict");
    expect(ict.layers.ict && ict.layers.unicorn && ict.layers.gaps).toBe(true);
    const full = viewFor("full");
    expect(full.layers.fvg && full.layers.zones && full.layers.signals && full.tags.bsl && full.tags.eq).toBe(true);
  });
});
