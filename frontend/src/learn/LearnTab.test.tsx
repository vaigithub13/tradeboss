import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const createChart = vi.hoisted(() => vi.fn());

vi.mock("lightweight-charts", () => {
  const series = {
    setData: vi.fn(),
    update: vi.fn(),
    applyOptions: vi.fn(),
    createPriceLine: vi.fn(() => ({})),
    removePriceLine: vi.fn(),
    attachPrimitive: vi.fn(),
    priceToCoordinate: vi.fn(() => 0),
    coordinateToPrice: vi.fn(() => 100),
  };
  createChart.mockImplementation(() => ({
    addSeries: () => series,
    applyOptions: vi.fn(),
    remove: vi.fn(),
    subscribeClick: vi.fn(),
    subscribeCrosshairMove: vi.fn(),
    timeScale: () => ({ timeToCoordinate: () => 0 }),
  }));
  return {
    createChart,
    CandlestickSeries: class {},
    ColorType: { Solid: "solid" },
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 },
    createSeriesMarkers: () => ({ setMarkers: vi.fn() }),
  };
});

import { chartHidden } from "../chart/controls";
import { LearnTab } from "./LearnTab";
import { useChartStore } from "../store/chartStore";

function ist(day: number, hour: number, minute: number): number {
  return Math.floor(Date.UTC(2026, 10, day, hour - 5, minute - 30) / 1000);
}

const bars = [
  { time: ist(25, 15, 15), open: 95, high: 100, low: 80, close: 95 },
  { time: ist(26, 9, 15), open: 100, high: 110, low: 90, close: 100 },
  { time: ist(26, 9, 30), open: 100, high: 120, low: 88, close: 112 },
  { time: ist(26, 9, 45), open: 112, high: 115, low: 100, close: 108 },
];

describe("Learn tab", () => {
  afterEach(() => cleanup());

  it("hides a layer, credits Lightweight Charts, and does not touch the Chart tab symbol", async () => {
    const user = userEvent.setup();
    useChartStore.setState({ symbol: "NIFTY50" });
    render(<LearnTab bars={bars} />);
    expect(createChart).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
      layout: expect.objectContaining({ attributionLogo: false, textColor: "#374151", fontSize: 12 }),
    }));
    const options = createChart.mock.calls[0]?.[1] as {
      timeScale: { tickMarkFormatter: (time: number, type: number) => string };
      localization: { timeFormatter: (time: number) => string };
    };
    expect(options.timeScale.tickMarkFormatter(ist(26, 9, 15), 3)).toBe("09:15");
    expect(options.timeScale.tickMarkFormatter(ist(26, 15, 30), 3)).toBe("15:30");
    expect(options.localization.timeFormatter(ist(26, 9, 15))).toContain("09:15");
    expect(options.localization.timeFormatter(ist(26, 15, 30))).toContain("15:30");
    expect(options.localization.timeFormatter(ist(26, 9, 15))).not.toContain("03:45");
    expect(screen.getByRole("button", { name: "5m" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "30m" })).toBeNull();
    expect(screen.getByLabelText("Labels shown")).toHaveValue(12);
    expect(screen.queryByTestId("learn-labels")).toBeNull();
    await user.click(screen.getByRole("tab", { name: "Signals" }));
    expect(screen.getByTestId("learn-signals-banner").textContent).toBe(
      "👀 Watch only — these are not trades. Each one is scored automatically.",
    );
    expect(screen.getByRole("button", { name: "Run the review now" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Export for review" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "What works" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "history (no costs)" })).toBeTruthy();
    expect(screen.getByTestId("learn-banner").textContent).toMatch(/Opening hour — accumulation forming/);
    const credits = screen.getByTestId("learn-credits");
    expect(credits.textContent).toMatch(/Read-only study chart — never places trades/);
    expect(credits.querySelector("a")?.getAttribute("href")).toBe("https://www.tradingview.com/lightweight-charts/");
    expect(chartHidden("learn")).toBe(true);
    await user.click(screen.getByRole("tab", { name: "Events" }));
    await user.click(screen.getAllByTestId("learn-event")[0]!);
    const card = screen.getByTestId("learn-explain");
    expect(card.textContent).toMatch(/On this chart/);
    expect(card.textContent).toMatch(/What it is/);
    expect(card.textContent).toMatch(/Why it matters/);
    expect(card.textContent).toMatch(/What to watch next/);
    await user.click(screen.getByRole("button", { name: "SENSEX" }));
    expect(useChartStore.getState().symbol).toBe("NIFTY50");
  });
});
