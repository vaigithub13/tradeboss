import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SensexMarketView, type SensexViewState } from "./SensexMarketView";

const baseView = {
  day: "2026-11-20",
  built_at: 1,
  symbol: "SENSEX",
  factors: [],
  score: 0,
  bias: "Neutral",
  expected_range: null,
  expected_move: null as null,
  day_type: "mixed" as const,
  day_type_reason: "",
  levels: [],
  plan: "Sensex: watch CPR.",
};

describe("SensexMarketView straddle unavailable", () => {
  it("shows the backend reason instead of a generic message", () => {
    const state: SensexViewState = {
      view: {
        ...baseView,
        expected_move_reason: "no chain for expiry 2026-11-20: HTTP 400",
      },
      error: null,
      loading: false,
      refresh: () => undefined,
    };
    const { getByTestId } = render(
      <SensexMarketView state={state} showLevels={false} onShowLevels={() => undefined} />,
    );
    expect(getByTestId("sensex-straddle-unavailable").textContent).toContain(
      "no chain for expiry 2026-11-20: HTTP 400",
    );
  });

  it("falls back to the legacy generic text when no reason is set", () => {
    const state: SensexViewState = {
      view: { ...baseView },
      error: null,
      loading: false,
      refresh: () => undefined,
    };
    const { getByTestId } = render(
      <SensexMarketView state={state} showLevels={false} onShowLevels={() => undefined} />,
    );
    expect(getByTestId("sensex-straddle-unavailable").textContent).toBe(
      "Unavailable (no option chain)",
    );
  });
});
