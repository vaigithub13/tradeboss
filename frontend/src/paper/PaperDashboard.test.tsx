import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PaperDashboard } from "./PaperDashboard";
import { useAlertStore } from "../store/alertStore";
import { usePaperStore } from "../store/paperStore";
import { usePaperTradesStore } from "../store/paperTradesStore";
import type { PaperTradeWithReal } from "../api/paper";
import type { PaperTradeRow } from "./trades";

vi.mock("../api/paper", async () => {
  const actual = await vi.importActual<typeof import("../api/paper")>("../api/paper");
  return {
    ...actual,
    fetchPaperWeek: vi.fn(async () => ({ week: "2026-11-10", totals: { trades: 1, net: 10 }, exits: {}, excluded: [] })),
    fetchRealTrades: vi.fn(async () => ({ rows: [], totals: null, settings: { account_stop: 5000 } })),
  };
});

vi.mock("./PaperEquityChart", () => ({
  PaperEquityChart: ({ series }: { series: unknown[] }) => (
    <div data-testid="paper-equity-chart">{series.length ? `${series.length} points` : "empty"}</div>
  ),
}));

vi.mock("./sounds", () => ({
  playAlertSound: vi.fn(),
  unlockAudio: vi.fn(async () => true),
}));

function trade(
  partial: Partial<PaperTradeRow> & Pick<PaperTradeRow, "slot" | "date" | "net" | "entry_time">,
): PaperTradeWithReal {
  const base: PaperTradeRow = {
    direction: partial.direction ?? "CE",
    side: partial.side ?? "LONG",
    signal_time: null,
    trigger_index: null,
    fill_time: partial.entry_time,
    contract: partial.contract ?? "NIFTY 25000 CE 16 NOV 26",
    entry_premium: 100,
    entry_source: "quote",
    units: 65,
    rule: null,
    index_stop: null,
    index_target: null,
    premium_stop: 80,
    premium_target: 140,
    estimated: [],
    exit_time: partial.exit_time ?? partial.entry_time + 600,
    exit_premium: 110,
    exit_reason: partial.exit_reason ?? "target",
    exit_reason_raw: "target",
    risk: null,
    r_multiple: 1,
    index_entry: 25000,
    index_exit: 25050,
    lots: 1,
    lot_size: 65,
    nifty_points: 50,
    premium_points: 10,
    gross: 650,
    charges: 20,
    source: "live",
    index_from_candles: [],
    shadow: null,
    shadow_net: Number(partial.net ?? 0),
    slot: partial.slot,
    date: partial.date,
    entry_time: partial.entry_time,
    net: Number(partial.net ?? 0),
  };
  return { ...base, paper_key: `${base.slot}:${base.entry_time}`, real: null, real_vs_paper: null };
}

describe("PaperDashboard", () => {
  afterEach(() => cleanup());
  beforeEach(() => {
    try {
      localStorage.clear();
    } catch {
      /* ignore */
    }
    usePaperStore.setState({
      status: {
        state: "running",
        slots: [
          {
            slot: "5",
            state: "running",
            ended_by: null,
            day: "2026-11-16",
            strategy: "dd_five_confirm",
            params: { exit_rule: "premium_trail_10" },
            wanted_keys: [],
            signals: [],
            trades: [],
            summary: { trades: 1, wins: 0, net: -50, modelled_legs: 0, shadow_net: -40, shadow_diff: 10 },
            position: {
              symbol: "NIFTY 25200 CE 17 NOV 26",
              entry: { price: 200 },
              levels: { premium_stop: 180, premium_target: null },
            },
            mark: { symbol: "NIFTY 25200 CE 17 NOV 26", source: "quote", gross: -10, net: -25 },
          },
          {
            slot: "2",
            state: "stopped",
            ended_by: "session-end",
            day: "2026-11-16",
            strategy: "price_channel",
            params: {},
            wanted_keys: [],
            signals: [],
            trades: [],
            summary: { trades: 0, wins: 0, net: 0, modelled_legs: 0 },
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
    usePaperTradesStore.setState({
      open: false,
      full: false,
      rows: [
        trade({ slot: "2", date: "2026-11-14", net: 100, entry_time: 1, direction: "CE" }),
        trade({ slot: "5", date: "2026-11-15", net: -40, entry_time: 2, direction: "CE" }),
        trade({ slot: "1", date: "2026-11-15", net: 9999, entry_time: 3, direction: "PE" }),
        trade({
          slot: "6",
          date: "2026-11-16",
          net: 50,
          entry_time: 4,
          direction: "PE",
          side: "SHORT",
          contract: "NIFTY 25000 PE 16 NOV 26",
        }),
      ],
      totals: null,
      accountStop: 5000,
      loading: false,
      error: null,
      filter: { slot: "", from: "", to: "" },
      sort: { key: "entryTime", dir: "desc" },
      selected: null,
      overlay: null,
      focus: null,
      setOpen: () => {},
      toggleFull: () => {},
      load: async () => {},
      setFilter: () => {},
      sortBy: () => {},
      select: async () => {},
      clearSelection: () => {},
      setAccountStop: async () => {},
    });
    useAlertStore.setState({ soundOn: true, volume: 0.5, slots: {} });
  });

  it("renders KPIs, active cards only, and live trade box", () => {
    render(<PaperDashboard />);
    expect(screen.getByTestId("paper-dashboard")).toBeInTheDocument();
    expect(screen.getByTestId("dash-slot-5")).toBeInTheDocument();
    expect(screen.getByTestId("dash-slot-2")).toBeInTheDocument();
    expect(screen.queryByTestId("dash-slot-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("retired-note")).toHaveTextContent("Retired");
    expect(screen.getByTestId("dash-live-5")).toHaveTextContent("CE");
    expect(screen.getByTestId("dash-live-5")).toHaveTextContent("SL");
  });

  it("renders empty equity when no active trades after filter wipe", () => {
    usePaperTradesStore.setState({ rows: [] });
    render(<PaperDashboard />);
    // PaperEquityChart is mocked; empty series renders the "empty" label
    expect(screen.getByTestId("paper-equity-chart")).toHaveTextContent("empty");
  });

  it("filters trades by side", async () => {
    const user = userEvent.setup();
    render(<PaperDashboard />);
    await user.selectOptions(screen.getByLabelText("Side filter"), "PE");
    const table = screen.getByLabelText("Trades");
    const bodyRows = within(table).getAllByRole("row").filter((r) => r.parentElement?.tagName === "TBODY");
    expect(bodyRows).toHaveLength(1);
    expect(bodyRows[0]?.textContent).toMatch(/25000 PE/);
    expect(bodyRows[0]?.textContent).not.toMatch(/25000 CE/);
  });

  it("shows backend offline when poll error set", () => {
    usePaperStore.setState({ errors: { "": "Failed to fetch" } });
    render(<PaperDashboard />);
    expect(screen.getByTestId("paper-offline")).toHaveTextContent("backend offline");
  });
});
