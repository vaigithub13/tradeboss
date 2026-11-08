import { describe, expect, it } from "vitest";

import type { OptionsChainResponse } from "../api/options";
import { buildKeyLevelsLadder } from "./levels";

/** Friday 7 Nov close shape: Spot between R1 and PIVOT. */
const fridayClose: OptionsChainResponse = {
  available: true,
  expiry: "2026-11-09",
  as_of: 0,
  as_of_ist: "2026-11-07T15:30:00+05:30",
  status: "MARKET_CLOSED",
  spot: 25030.35,
  atm: 25050,
  resistance: [
    { strike: 25200, oi: 5_842_915, tag: "R1", strength: "Medium" },
    { strike: 25300, oi: 5_732_610, tag: "R2", strength: "Medium" },
    { strike: 25500, oi: 10_172_045, tag: "R3", strength: "Strong" },
  ],
  support: [
    { strike: 24500, oi: 9_176_960, tag: "S1", strength: "Strong" },
    { strike: 24400, oi: 8_711_300, tag: "S2", strength: "Strong" },
    { strike: 24300, oi: 6_782_750, tag: "S3", strength: "Medium", edge: true },
  ],
  pivot: {
    strike: 25000,
    call_oi: 11_206_325,
    put_oi: 4_889_690,
    heavy_call: true,
    heavy_put: false,
    tag: "PIVOT",
    read: "Above = bullish, short covering risk for call writers",
  },
  rows: [],
};

describe("buildKeyLevelsLadder", () => {
  it("sorts Friday close strictly by price: R3→R2→R1→Spot→PIVOT→S1→S2→S3", () => {
    const keys = buildKeyLevelsLadder(fridayClose).map((item) => item.key);
    expect(keys).toEqual(["R3", "R2", "R1", "SPOT", "PIVOT", "S1", "S2", "S3"]);
    const prices = buildKeyLevelsLadder(fridayClose).map((item) => item.price);
    expect(prices).toEqual([25500, 25300, 25200, 25030.35, 25000, 24500, 24400, 24300]);
  });

  it("merges R1 with PIVOT when they share a strike", () => {
    const merged = buildKeyLevelsLadder({
      ...fridayClose,
      resistance: [
        { strike: 25000, oi: 11_000_000, tag: "R1", strength: "Strong" },
        { strike: 25200, oi: 5_000_000, tag: "R2", strength: "Medium" },
      ],
      pivot: { ...fridayClose.pivot!, strike: 25000 },
    });
    const labels = merged.map((i) => i.label ?? i.key);
    expect(labels).toContain("R1 / PIVOT");
    expect(labels.filter((l) => l === "PIVOT" || l === "R1")).toEqual([]);
    expect(merged.filter((i) => Math.abs(i.price - 25000) < 1e-6)).toHaveLength(1);
  });
});
