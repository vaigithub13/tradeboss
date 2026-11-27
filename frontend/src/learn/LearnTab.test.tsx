import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const createChart = vi.hoisted(() => vi.fn());
const setData = vi.hoisted(() => vi.fn());

vi.mock("lightweight-charts", () => {
  const series = {
    setData,
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
import { lastSessionOpen } from "./replay";
import { toIstInput } from "./time";
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

  it("shows candles up to the replay cursor and steps one at a time", async () => {
    const user = userEvent.setup();
    const series = [15, 20, 25, 30, 35, 40, 45, 50].map((minute, index) => ({
      time: ist(26, 9, minute),
      open: 100,
      high: 110 + index,
      low: 90,
      close: 100 + index,
    }));
    series.push({ time: ist(26, 10, 0), open: 120, high: 130, low: 110, close: 125 });
    setData.mockClear();
    render(<LearnTab bars={series} />);
    await user.click(screen.getByTestId("learn-replay"));
    expect(screen.getByLabelText("Replay cursor")).toHaveValue(
      toIstInput(lastSessionOpen(Math.floor(Date.now() / 1000))),
    );
    fireEvent.change(screen.getByLabelText("Replay cursor"), { target: { value: "2026-11-26T09:45" } });
    const drawn = () => (setData.mock.calls.at(-1)?.[0] as { time: number }[]).map((bar) => bar.time);
    const until = (minute: number) => series.filter((bar) => bar.time <= ist(26, 9, minute)).map((bar) => bar.time);
    expect(drawn()).toEqual(until(45));
    expect(drawn().some((time) => time > ist(26, 9, 45))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Step" }));
    expect(drawn()).toEqual(until(50));
    expect(drawn()).toHaveLength(until(45).length + 1);
    expect(drawn().some((time) => time > ist(26, 9, 50))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(drawn()).toEqual(until(45));
  });
});
