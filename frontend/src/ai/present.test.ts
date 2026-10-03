import { describe, expect, it } from "vitest";

import { ANALYSIS_LABEL, HORIZONS, canPlaceOrders, costText, hitRateText, levelLines } from "./present";

const analysis = {
  trends: { "5m": "up", "15m": "up", "1h": "sideways", "1D": "down" },
  bias: "bull" as const,
  key_levels: [
    { price: 99, kind: "support" as const, label: "swing low" },
    { price: 101, kind: "resistance" as const, label: "swing high" },
  ],
  patterns: ["higher lows"],
  bull: { trigger: 102, invalidation: 98, note: "holds" },
  bear: { trigger: 98, invalidation: 102, note: "fails" },
  confidence: 0.5,
  reasoning: "The last swing low is intact.",
};

describe("AI analysis panel", () => {
  it("labels the panel as context and cannot place an order", () => {
    expect(ANALYSIS_LABEL).toBe("AI analysis: context, not a trade signal");
    expect(canPlaceOrders()).toBe(false);
  });

  it("draws levels and scenario prices without an order title", () => {
    const lines = levelLines(analysis);
    expect(lines.map((line) => line.title)).toEqual([
      "swing low",
      "swing high",
      "Bull trigger",
      "Bull invalidation",
      "Bear trigger",
      "Bear invalidation",
    ]);
    expect(lines.map((line) => line.price)).toEqual([99, 101, 102, 98, 98, 102]);
    expect(lines.every((line) => !/order|buy|sell/i.test(line.title))).toBe(true);
    expect(new Set(lines.map((line) => line.kind))).toEqual(new Set(["support", "resistance", "trigger", "invalidation"]));
  });

  it("names the short horizons beside the session horizons", () => {
    expect(HORIZONS.map((horizon) => horizon.label)).toEqual([
      "60 minutes later",
      "same-session close",
      "1 session",
      "3 sessions",
      "5 sessions",
    ]);
  });

  it("shows the AI track record against both baselines", () => {
    expect(hitRateText({
      label: "1 session",
      scored: 4,
      ai: { bias: 0.5, triggers: 1, levels: null },
      alwaysBullish: { bias: 0.25 },
      followTrend: { bias: 0.75 },
    })).toEqual([
      "1 session · scored 4",
      "AI bias 50% · always bullish 25% · follow the trend 75%",
      "AI triggers 100%",
      "AI levels —",
    ]);
  });

  it("shows tokens, and a dollar amount only when both rates are set", () => {
    const usage = { prompt_tokens: 100, completion_tokens: 50 };
    expect(costText(usage, {})).toBe("100 in / 50 out");
    expect(costText(usage, { inputUsdPerMtok: 2.5, outputUsdPerMtok: 10 })).toBe("100 in / 50 out · $0.000750");
  });
});
