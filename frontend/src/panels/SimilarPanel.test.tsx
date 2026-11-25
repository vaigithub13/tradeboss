import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SimilarPanel } from "./SimilarPanel";

const candles = {
  symbol: "NIFTY50",
  timeframe: "5m",
  window: 30,
  as_of: null,
  method: "euclidean",
  note: "Read-only context. Not a signal. No order is sent.",
  window_rule: "Intraday windows that cross the overnight gap are excluded. The gap is not a feature.",
  query: { start: 1, end: 2, forward_end: 3 },
  matches: [],
  summary: {
    "5": { matches: { n: 0, pct_up: null, median_return: null, spread: null }, base: { n: 10, pct_up: 51, median_return: 0, spread: 1 } },
    "10": { matches: { n: 0, pct_up: null, median_return: null, spread: null }, base: { n: 10, pct_up: 51, median_return: 0, spread: 1 } },
    "20": { matches: { n: 0, pct_up: null, median_return: null, spread: null }, base: { n: 10, pct_up: 51, median_return: 0, spread: 1 } },
  },
  reliability: {
    "10": {
      n: 6,
      hit_rate: 80,
      base_rate: 50,
      up_rate: 50,
      verdict: "above the base rate",
      sample_size: 6,
      evaluated: 6,
      runtime_s: 1,
      capped: false,
    },
  },
};

const days = {
  symbol: "NIFTY50",
  at: "10:15",
  clock: "10:15",
  as_of: null,
  date: "2024-01-08",
  note: candles.note,
  window_rule: "Features stop at the chosen clock.",
  matches: [],
  outcomes: {
    trend: { label: "trend day", hits: 6, n: 8, base_rate: 0.31, base_n: 100, line: "trend day: 6/8 similar vs 31% all days" },
  },
  reliability: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SimilarPanel", () => {
  it("shows the base-rate line and does not colour a verdict with n under 10", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () => (String(url).includes("/days") ? days : candles),
        text: async () => JSON.stringify(String(url).includes("/days") ? days : candles),
      })),
    );
    const { getByTestId } = render(
      <SimilarPanel symbol="NIFTY50" asOf={null} onOpenMatch={() => undefined} onClose={() => undefined} />,
    );
    await waitFor(() => {
      expect(getByTestId("similar-note").textContent).toContain("Not a signal");
    });
    expect(getByTestId("day-trend day").textContent).toContain("6/8 similar vs 31% all days");
    expect(getByTestId("day-trend day").className).toBe("text-white/70");
    expect(getByTestId("reliability-10").className).toBe("text-white/70");
    expect(getByTestId("reliability-10").textContent).toContain("above the base rate");
  });
});
