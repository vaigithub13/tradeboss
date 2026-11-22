import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ScannerPanel } from "./ScannerPanel";
import { saveScannerSettings, type ScannerSettings } from "../api/scanner";

vi.mock("../scanner/ScannerChart", () => ({
  ScannerChart: () => <div data-testid="scanner-chart" />,
}));

vi.mock("../paper/sounds", () => ({
  unlockAudio: vi.fn(async () => true),
  playAlertSound: vi.fn(),
}));

vi.mock("../api/scanner", () => ({
  fetchScannerStatus: vi.fn(),
  fetchScannerSignals: vi.fn(),
  fetchScannerHeatmap: vi.fn(),
  fetchScannerTrades: vi.fn(),
  fetchScannerReport: vi.fn(),
  fetchUniverses: vi.fn(),
  fetchRankings: vi.fn(),
  fetchScannerSettings: vi.fn(),
  saveScannerSettings: vi.fn(),
  scanNow: vi.fn(),
}));

import {
  fetchRankings,
  fetchScannerHeatmap,
  fetchScannerReport,
  fetchScannerSettings,
  fetchScannerSignals,
  fetchScannerStatus,
  fetchScannerTrades,
  fetchUniverses,
  scanNow,
} from "../api/scanner";

const settings: ScannerSettings = {
  account_size: 200000,
  max_loss_per_trade: 500,
  daily_loss_limit: 1500,
  max_trades_per_day: 3,
  max_capital_per_trade: 50000,
  min_rr: 2,
  entry_mode: "trigger_close",
  orb_stop: "midpoint",
  target_mode: "2r",
  trail_breakeven: false,
  tm_paths: 10000,
  tm_seed: 20261123,
  tm_edge_points: 10,
  tm_min_win_rate: 0.4,
  tm_min_fires: 8,
  universe: "nifty50",
  only_proven: false,
  auto_trade: true,
  live_subscribe: true,
  slippage_ticks: 1,
  tick_size: 0.05,
  setups: { orb15: true, flag: true, rsi_extreme: true, vwap_cross: true, gap_go: true, gap_fill: true },
};

function prime() {
  vi.mocked(fetchScannerStatus).mockResolvedValue({
    now: "2026-11-23T09:35:00+05:30",
    trading_day: true,
    universe: "nifty50",
    universe_label: "Nifty 50",
    universes: ["nifty50", "banknifty"],
    unmapped: [],
    net: 120,
    trades_used: 1,
    max_trades: 3,
    loss_used: 0,
    daily_loss_limit: 1500,
    minutes_to_cutoff: 295,
    minutes_to_flat: 340,
    auto_trade: true,
    alerts: [],
    open: [],
  });
  vi.mocked(fetchScannerSignals).mockResolvedValue({
    signals: [
      { id: "b", symbol: "INFY", setup: "orb15", side: "long", status: "triggered", rr: 1, verdict: "AVOID", decision: "BLOCK" },
      { id: "a", symbol: "HDFCBANK", setup: "flag", side: "long", status: "triggered", rr: 3, verdict: "GOOD", decision: "PASS", entry: 100, stop: 99, target: 102, qty: 10, histogram: { target: 70, stop: 20, open: 10 }, occurrences: [] },
    ],
  });
  vi.mocked(fetchScannerHeatmap).mockResolvedValue({
    rows: [{ symbol: "HDFCBANK", sector: "Financial Services", change_pct: 0.4, vwap_dist_pct: 0.1, rvol: 1.2, vs_nifty: 0.2 }],
  });
  vi.mocked(fetchScannerTrades).mockResolvedValue({ trades: [] });
  vi.mocked(fetchScannerReport).mockResolvedValue({ report: null });
  vi.mocked(fetchUniverses).mockResolvedValue({
    universes: { nifty50: { label: "Nifty 50", symbols: ["HDFCBANK"] }, banknifty: { label: "Nifty Bank", symbols: ["HDFCBANK"] } },
    selected: "nifty50",
  });
  vi.mocked(fetchRankings).mockResolvedValue({
    as_of: "2026-11-21",
    pairs: [{ symbol: "HDFCBANK", setup: "orb15", trades: 9, win_rate: 0.5, avg_r: 0.2, profit_factor: 1.4, max_drawdown: 100, net: 250 }],
  });
  vi.mocked(fetchScannerSettings).mockResolvedValue(settings);
  vi.mocked(saveScannerSettings).mockImplementation(async (patch) => ({ ...settings, ...patch }) as ScannerSettings);
  vi.mocked(scanNow).mockResolvedValue({} as never);
}

describe("Stock Scanner tab", () => {
  beforeEach(() => prime());
  afterEach(() => cleanup());

  it("sorts the signal table and opens a row", async () => {
    const user = userEvent.setup();
    render(<ScannerPanel />);
    const table = await screen.findByTestId("scanner-signals");
    const body = () => [...table.querySelectorAll("tbody tr")].map((row) => row.textContent ?? "");
    expect(body()[0]).toContain("HDFCBANK");
    await user.click(screen.getByRole("button", { name: "R:R" }));
    expect(body()[0]).toContain("INFY");
    expect(body()[1]).toContain("HDFCBANK");
    const hdfc = [...table.querySelectorAll("tbody tr")].find((row) => row.textContent?.includes("HDFCBANK"));
    await user.click(hdfc!);
    expect(screen.getByTestId("scanner-chart")).toBeInTheDocument();
    expect(screen.getByTestId("scanner-histogram")).toBeInTheDocument();
    expect(screen.getByTestId("scanner-heatmap")).toHaveTextContent("Financial Services");
  });

  it("switches universe and round-trips settings", async () => {
    const user = userEvent.setup();
    render(<ScannerPanel />);
    await screen.findByTestId("scanner-universe");
    await user.selectOptions(screen.getByTestId("scanner-universe"), "banknifty");
    expect(saveScannerSettings).toHaveBeenCalledWith({ universe: "banknifty" });
    await user.click(screen.getByTestId("scanner-settings"));
    const trades = await screen.findByLabelText("Max trades");
    await user.clear(trades);
    await user.type(trades, "4");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(saveScannerSettings).toHaveBeenCalledWith(expect.objectContaining({ max_trades_per_day: 4 }));
    await user.click(screen.getByRole("button", { name: "Rankings" }));
    expect(screen.getByTestId("scanner-rankings")).toHaveTextContent("orb15");
  });
});
