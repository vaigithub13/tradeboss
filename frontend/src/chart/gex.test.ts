import { describe, expect, it } from "vitest";

import type { GexResponse } from "../api/gex";
import { formatCrore, gexBars, gexLevels, gexSummary } from "./gex";

const base: GexResponse = {
  available: true,
  expiry: "2026-11-10",
  as_of: 1793952000,
  unit: "₹ crore per 1% move",
  spot: 25000,
  strikes: [
    { strike: 23000, call: 0, put: -900, net: -900 },
    { strike: 24900, call: 10, put: -50, net: -40 },
    { strike: 25000, call: 80, put: -20, net: 60 },
    { strike: 25100, call: 120, put: -10, net: 110 },
  ],
  total_net: 130,
  regime: "positive",
  call_wall: 25100,
  put_wall: 24900,
  gamma_flip: 24950.4,
};

describe("gex helpers", () => {
  it("formats crores with a sign", () => {
    expect(formatCrore(1234.4)).toBe("+1,234 Cr");
    expect(formatCrore(-5.678)).toBe("−5.68 Cr");
    expect(formatCrore(12.34)).toBe("+12.3 Cr");
  });

  it("scales bars near spot to the largest one and drops far strikes", () => {
    const bars = gexBars(base.strikes, 25000);
    expect(bars.map((b) => b.price)).toEqual([24900, 25000, 25100]);
    expect(bars[2]).toEqual({ price: 25100, frac: 1, positive: true });
    expect(bars[0]?.positive).toBe(false);
    expect(bars[0]?.frac).toBeCloseTo(40 / 110);
  });

  it("returns no bars when nothing is near spot", () => {
    expect(gexBars([{ strike: 20000, call: 1, put: 0, net: 1 }], 25000)).toEqual([]);
  });

  it("makes price lines for flip and walls", () => {
    expect(gexLevels(base).map((l) => l.title)).toEqual(["γ flip", "call wall", "put wall"]);
    expect(gexLevels({ ...base, gamma_flip: null, put_wall: null }).map((l) => l.title)).toEqual(["call wall"]);
  });

  it("summarises the regime", () => {
    expect(gexSummary(base)).toContain("Net GEX +130 Cr");
    expect(gexSummary(base)).toContain("pin");
    expect(gexSummary({ ...base, regime: "negative", total_net: -5 })).toContain("trend");
    expect(gexSummary({ ...base, available: false, reason: "option chain is empty" })).toBe(
      "GEX unavailable: option chain is empty",
    );
  });
});
