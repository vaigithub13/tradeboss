import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PaperStatus } from "../api/paper";
import { ExpiryStraddleCard, EXPIRY_STRADDLE_NOTE } from "./ExpiryStraddleCard";

vi.mock("../api/paper", async () => {
  const actual = await vi.importActual<typeof import("../api/paper")>("../api/paper");
  return {
    ...actual,
    fetchPaperWeek: vi.fn(async () => ({
      week: "2026-W49",
      totals: { trades: 1, net: 1200.5, gross: 1800, costs: 599.5 },
      exits: {},
      excluded: [],
    })),
  };
});

const ENTRY = (9 * 60 + 20) * 60_000;
const EXIT = (15 * 60 + 15) * 60_000;

function status(over: Partial<PaperStatus> = {}): PaperStatus {
  return {
    slot: "9",
    state: "running",
    ended_by: null,
    day: "2026-12-01",
    strategy: "expiry_straddle",
    params: {},
    wanted_keys: [],
    signals: [],
    trades: [],
    summary: null,
    position: null,
    mark: null,
    book_status: "done",
    book: {
      status: "done",
      spot_at_entry: 25010,
      strike: 25000,
      gross: 1800,
      costs: 599.5,
      net: 1200.5,
      legs: [
        { kind: "CE", entry: 119.5, stop: 155.35, exit: 200.5, reason: "stop", entry_ms: ENTRY, exit_ms: (10 * 60) * 60_000 },
        { kind: "PE", entry: 99.5, stop: 129.35, exit: 40.5, reason: "time", entry_ms: ENTRY, exit_ms: EXIT },
      ],
    },
    ...over,
  };
}

describe("Expiry Straddle card", () => {
  afterEach(() => cleanup());

  it("shows the book, both legs, charges, the week, and the research line", async () => {
    render(<ExpiryStraddleCard status={status()} />);
    expect(screen.getByTestId("expiry-straddle-note")).toHaveTextContent(EXPIRY_STRADDLE_NOTE);
    expect(screen.getByTestId("expiry-straddle-card")).toHaveTextContent("done");
    expect(screen.getByTestId("expiry-straddle-card")).toHaveTextContent("Spot 25010");
    expect(screen.getByTestId("expiry-straddle-card")).toHaveTextContent("Strike 25000");
    const ce = screen.getByTestId("expiry-leg-CE");
    expect(ce).toHaveTextContent("entry 119.5");
    expect(ce).toHaveTextContent("stop 155.35");
    expect(ce).toHaveTextContent("exit 200.5");
    expect(ce).toHaveTextContent("stop");
    expect(ce).toHaveTextContent("09:20:00 → 10:00:00");
    expect(screen.getByTestId("expiry-leg-PE")).toHaveTextContent("15:15:00");
    expect(await screen.findByText(/Week 2026-W49/)).toBeInTheDocument();
    expect(screen.getByTestId("expiry-straddle-pnl")).toHaveTextContent("Net");
    expect(screen.getByTestId("expiry-straddle-pnl")).toHaveTextContent("Gross");
    expect(screen.getByTestId("expiry-straddle-pnl")).toHaveTextContent("Charges");
    expect(screen.getByTestId("expiry-straddle-week")).toHaveTextContent("Charges");
  });

  it("says no trade on a non-expiry day", () => {
    render(
      <ExpiryStraddleCard
        status={status({
          day: "2026-12-02",
          book_status: "no_trade",
          book: { status: "no_trade", spot_at_entry: null, strike: null, legs: [], gross: 0, costs: 0, net: 0 },
        })}
      />,
    );
    expect(screen.getByTestId("expiry-straddle-card")).toHaveTextContent("no_trade");
    expect(screen.getByTestId("expiry-straddle-card")).toHaveTextContent("no trade today (not expiry)");
  });
});
