import { describe, expect, it } from "vitest";

import { placePatternPill, planEdges, rectsOverlap, signalPill, studyTip, zoneStudy } from "./chartLook";
import type { KeyZone } from "./zones";

function zone(part: Pick<KeyZone, "id" | "low" | "high" | "role" | "strength">): KeyZone {
  return {
    ...part,
    flipped: false,
    sources: ["PDH"],
    touches: 0,
    lastTest: null,
    label: "R",
    detail: "Resistance from the closed candles.",
    visible: true,
    faint: false,
  };
}

describe("Learn chart look", () => {
  it("pins a sell plan above and a buy plan below, with the distance from the last close", () => {
    const edges = planEdges([
      zone({ id: "r", low: 25795, high: 25810, role: "resistance", strength: 3 }),
      zone({ id: "s", low: 25550, high: 25560, role: "support", strength: 2 }),
    ], 25752.5);
    expect(edges.map((edge) => edge.text)).toEqual([
      "▲ Plan · SELL zone 25,795–25,810 · 42.5 above",
      "▼ Plan · BUY zone 25,550–25,560 · 192.5 below",
    ]);
    expect(edges[0]?.above).toBe(true);
    expect(edges[1]?.above).toBe(false);
    expect(edges[0]?.tip).toMatch(/🔴 sell study/);
    expect(edges[0]?.tip).toMatch(/study only — not a trade/);
  });

  it("keeps a pattern pill off the candle body", () => {
    const body = { x: 100, y: 40, w: 8, h: 20 };
    const above = placePatternPill(100, 40, 60, true);
    const below = placePatternPill(100, 40, 60, false);
    expect(rectsOverlap(above, body)).toBe(false);
    expect(rectsOverlap(below, body)).toBe(false);
    expect(above.x).toBeGreaterThan(body.x);
    expect(above.y + above.h).toBeLessThanOrEqual(body.y);
  });

  it("writes a two-to-four line study tooltip from the zone edges", () => {
    const study = zoneStudy(zone({ id: "r", low: 25795, high: 25810, role: "resistance", strength: 3 }), 25700);
    const tip = studyTip(study.tone, study.entry, study.sl, study.tp);
    const lines = tip.split("\n");
    expect(lines.length).toBeGreaterThanOrEqual(2);
    expect(lines.length).toBeLessThanOrEqual(4);
    expect(lines[0]).toBe("🔴 sell study");
    expect(tip).toMatch(/Entry idea 25,795\.0 · SL 25,810\.0 · TP 25,765\.0 · 2\.0R/);
    expect(lines.at(-1)).toBe("study only — not a trade");
    expect(studyTip("inside", 100, 99, 101).startsWith("⚪ inside zone")).toBe(true);
    expect(signalPill("sell", "open", "11:30", false)).toBe("● Plan SELL · 11:30 waiting");
    expect(signalPill("sell", "open", "11:30", true)).toBe("● Plan SELL · 11:30 in zone");
    expect(signalPill("buy", "TP", "11:30", false)).toBe("● Plan BUY · 11:30 TP");
    expect(signalPill("buy", "expired", "11:30", false)).toBe("● Plan BUY · 11:30 not counted");
  });
});