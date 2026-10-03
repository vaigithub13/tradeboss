import { describe, expect, it } from "vitest";

import { cardLines, convertEnabled, showBacktestCard, type PineCard } from "./present";

describe("pine panel gates", () => {
  it("keeps conversion disabled until the report is accepted", () => {
    expect(convertEnabled(false)).toBe(false);
    expect(convertEnabled(true)).toBe(true);
  });

  it("hides the backtest card for an indicator script", () => {
    expect(showBacktestCard("indicator")).toBe(false);
    expect(showBacktestCard("strategy")).toBe(true);
  });

  it("renders the summary warnings and the overnight split", () => {
    const card: PineCard = {
      warnings: [
        "cost rates are UNVERIFIED",
        "holdout not run",
        "unverified: stop=na behaviour on TradingView not reproduced",
      ],
      option_fill: "delta_adjusted",
      overnight_net: 12.5,
      same_day_net: -3,
      checks: ["look_ahead", "determinism", "tv_parity", "realistic"],
    };
    const lines = cardLines(card);
    expect(lines.some((line) => line.includes("UNVERIFIED"))).toBe(true);
    expect(lines.some((line) => line.includes("holdout not run"))).toBe(true);
    expect(lines.some((line) => line.includes("delta_adjusted"))).toBe(true);
    expect(lines.some((line) => line.includes("12.5") && line.includes("-3"))).toBe(true);
  });
});
