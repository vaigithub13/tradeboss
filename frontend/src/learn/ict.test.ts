import { describe, expect, it } from "vitest";

import { explainCard } from "./explainCards";
import {
  ICT_TERMS,
  killzoneNote,
  killzonesForDay,
  oteBand,
  scanIct,
  tradesZone,
  type OverlapZone,
} from "./ict";
import type { LearnBar, Structure } from "./structure";
import { istEpoch } from "./time";

function bar(day: number, hour: number, minute: number, high: number, low: number, close: number, open = close): LearnBar {
  return { time: istEpoch(2026, 11, day, hour, minute), open, high, low, close };
}

const drawn = ["ict_bull", "ict_bear", "brk_bull", "brk_bear", "uni_bull", "uni_bear", "ote", "eq", "premium", "discount", "kz", "sb", "ndog", "nwog", "mss"];

describe("ICT models", () => {
  it("places OTE and equilibrium on the leg", () => {
    const band = oteBand(100, 200, true);
    expect(band.eq).toBe(150);
    expect(band.low).toBeCloseTo(121);
    expect(band.high).toBeCloseTo(138);
    expect(band.sweet).toBeCloseTo(129.5);
    expect(band.high).toBeLessThan(band.eq);
    const sell = oteBand(100, 200, false);
    expect(sell.low).toBeCloseTo(162);
    expect(sell.high).toBeCloseTo(179);
  });

  it("reads the NSE killzone config", () => {
    expect(killzoneNote()).toMatch(/adapted for NSE/);
    const zones = killzonesForDay("2026-11-26");
    const byName = (name: string) => zones.filter((zone) => zone.name === name);
    expect(byName("Opening killzone").map((zone) => [zone.t1, zone.t2])).toEqual([
      [istEpoch(2026, 11, 26, 9, 15), istEpoch(2026, 11, 26, 10, 30)],
    ]);
    expect(byName("Silver Bullet (NSE)").map((zone) => [zone.t1, zone.t2])).toEqual([
      [istEpoch(2026, 11, 26, 10, 0), istEpoch(2026, 11, 26, 11, 0)],
      [istEpoch(2026, 11, 26, 13, 30), istEpoch(2026, 11, 26, 14, 30)],
    ]);
    expect(byName("Closing")[0]).toMatchObject({ t1: istEpoch(2026, 11, 26, 14, 30), t2: istEpoch(2026, 11, 26, 15, 15), kind: "kz" });
  });

  it("fills a day gap only after price trades the prior close, and a Friday gap is the week gap", () => {
    const thursday = [bar(26, 15, 20, 110, 100, 100, 108), bar(26, 15, 25, 108, 99, 100, 104)];
    const friday = [bar(27, 9, 15, 112, 108, 110, 108), bar(27, 9, 20, 112, 100, 101, 110)];
    const openOnly = scanIct([...thursday, friday[0]!], [], [], true);
    const dayGap = openOnly.boxes.find((box) => box.kind === "ndog");
    expect(dayGap?.state).toBe("open");
    expect(dayGap?.confirm).toBe(friday[0]!.time + 300);
    const filled = scanIct([...thursday, ...friday], [], [], true);
    expect(filled.boxes.find((box) => box.kind === "ndog")?.state).toBe("filled");
    expect(filled.events.some((event) => event.text === "NDOG filled")).toBe(true);
    const monday = [bar(30, 9, 15, 130, 120, 125, 122), bar(30, 9, 20, 128, 121, 124, 125)];
    const week = scanIct([...friday, ...monday], [], [], true);
    expect(week.boxes.some((box) => box.kind === "nwog")).toBe(true);
    expect(week.boxes.some((box) => box.kind === "ndog" && box.id.includes("2026-11-30"))).toBe(false);
  });

  it("does not tag an overlap until the later confirming candle has closed", () => {
    const bars = [
      bar(26, 10, 0, 100, 90, 92, 98),
      bar(26, 10, 5, 101, 93, 96, 94),
      bar(26, 10, 10, 112, 104, 110, 96),
      bar(26, 10, 15, 111, 103, 108, 110),
      bar(26, 10, 20, 118, 103, 116, 104),
    ];
    const events: Structure["events"] = [
      { index: 4, time: bars[4]!.time, kind: "bos", direction: "up", price: 112, fromTime: bars[2]!.time, swingIndex: 0 },
    ];
    const scan = scanIct(bars, events, [], true);
    const zone = scan.overlaps[0];
    expect(zone).toBeTruthy();
    expect(zone!.known).toBe(bars[4]!.time + 300);
    const early: OverlapZone = zone!;
    expect(tradesZone(early, bars[3]!, bars[3]!.time + 300, true)).toBe(false);
    expect(tradesZone(early, bars[4]!, bars[4]!.time + 300, true)).toBe(true);
    const hidden = scanIct(bars.slice(0, 4), events, [], true);
    expect(hidden.overlaps).toEqual([]);
  });

  it("gives every drawn ICT mark an explain card", () => {
    for (const kind of drawn) {
      const card = explainCard(kind);
      expect(card.title.length).toBeGreaterThan(3);
      expect(card.what.toLowerCase()).not.toContain("gold");
      expect(card.what.toLowerCase()).not.toContain("london");
      expect(card.why.length).toBeGreaterThan(10);
      expect(card.next.length).toBeGreaterThan(10);
    }
    const concepts = ICT_TERMS.filter((item) => item.chart.startsWith("not drawn"));
    expect(concepts.map((item) => item.term)).toEqual([
      "ICT 2022 Mentorship model",
      "Inversion FVG (IFVG)",
      "Balanced Price Range (BPR)",
      "Mitigation block",
    ]);
  });
});
