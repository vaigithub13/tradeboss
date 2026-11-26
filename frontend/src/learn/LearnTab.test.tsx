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
  };
  createChart.mockImplementation(() => ({
    addSeries: () => series,
    applyOptions: vi.fn(),
    remove: vi.fn(),
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
      layout: expect.objectContaining({ attributionLogo: false, textColor: "#1c2330", fontSize: 10 }),
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
    expect(screen.getByLabelText("Swing labels")).toHaveValue(12);
    expect(screen.queryByTestId("learn-labels")).toBeNull();
    expect(screen.queryByTitle("Previous day's high.")).toBeNull();
    await user.click(screen.getByTestId("learn-legend"));
    const labels = await screen.findByTestId("learn-labels");
    expect(labels.textContent).toMatch(/PDH/);
    expect(screen.getByTitle("Previous day's high.")).toBeTruthy();
    await user.click(screen.getByTestId("learn-toggle-pdh"));
    expect(screen.getByTestId("learn-labels").textContent).not.toMatch(/PDH/);
    await user.click(screen.getByTestId("learn-about"));
    const credits = screen.getByTestId("learn-credits");
    expect(credits.textContent).toMatch(/Charts: TradingView Lightweight Charts/);
    expect(credits.querySelector("a")?.getAttribute("href")).toBe("https://www.apache.org/licenses/LICENSE-2.0");
    await user.click(screen.getByRole("button", { name: "SENSEX" }));
    expect(useChartStore.getState().symbol).toBe("NIFTY50");
  });
});
