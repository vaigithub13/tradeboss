import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PaperPanel } from "../panels/PaperPanel";
import { useAlertStore } from "../store/alertStore";
import { usePaperStore } from "../store/paperStore";

vi.mock("../paper/sounds", () => ({
  unlockAudio: vi.fn(async () => true),
  playAlertSound: vi.fn(),
}));

const liveSlot = {
  slot: "5",
  state: "running" as const,
  ended_by: null,
  day: "2026-11-16",
  strategy: "dd_five_confirm",
  params: {},
  wanted_keys: [],
  signals: [],
  trades: [],
  summary: { trades: 2, wins: 1, net: 500, modelled_legs: 0 },
  position: {
    symbol: "NIFTY 25200 CE 17 NOV 26",
    entry: { price: 210 },
    levels: { premium_stop: 189, premium_target: null },
  },
  mark: { symbol: "NIFTY 25200 CE 17 NOV 26", source: "quote" as const, gross: -20, net: -45 },
};

describe("PaperPanel compact", () => {
  afterEach(() => cleanup());
  beforeEach(() => {
    usePaperStore.setState({
      status: {
        state: "running",
        slots: [
          { ...liveSlot, slot: "2", position: null, mark: null, summary: { trades: 1, wins: 0, net: -100, modelled_legs: 0 } },
          { ...liveSlot, slot: "4", position: null, mark: null, strategy: "price_channel" },
          liveSlot,
          { ...liveSlot, slot: "6", strategy: "dd_five_confirm", position: null, mark: null },
          {
            ...liveSlot,
            slot: "1",
            strategy: "log_xz",
            summary: { trades: 9, wins: 9, net: 9999, modelled_legs: 0 },
            position: null,
            mark: null,
          },
        ],
      },
      errors: {},
      refresh: async () => {},
      start: async () => {},
      stop: async () => {},
    });
    useAlertStore.setState({ soundOn: false, volume: 0.5, slots: {} });
  });

  it("renders at fixed 360px without retired slot cards", () => {
    const { container } = render(<PaperPanel />);
    const panel = screen.getByTestId("paper-side-panel");
    expect(panel).toHaveStyle({ width: "360px" });
    expect(screen.getByTestId("paper-live-card-5")).toBeInTheDocument();
    expect(screen.getByTestId("paper-live-card-2")).toBeInTheDocument();
    expect(screen.queryByTestId("paper-live-card-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("paper-live-card-3")).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/params JSON|Test entry sound|resting-orders/i);
    expect(screen.getByTestId("live-position")).toHaveTextContent("CE");
    expect(screen.getByTestId("live-position")).toHaveTextContent("25200");
    expect(screen.getByTestId("expiry-straddle-note")).toHaveTextContent("Research: build PF 1.60, validate PF 2.20, forward PF 2.80");
  });

  it("opens dashboard via callback", async () => {
    const user = userEvent.setup();
    const open = vi.fn();
    render(<PaperPanel onOpenDashboard={open} />);
    await user.click(screen.getByTestId("open-paper-dashboard"));
    expect(open).toHaveBeenCalled();
  });

  it("shows today summary from active slots only", () => {
    render(<PaperPanel />);
    const summary = screen.getByTestId("paper-today-summary");
    //  -100 + 500 + 500 + 0 from slots 2,4,5,6 summaries — slot 4 and 6 copied liveSlot net 500
    expect(summary.textContent).toMatch(/trades/);
    expect(summary.textContent).not.toMatch(/\+9,999/);
  });
});
