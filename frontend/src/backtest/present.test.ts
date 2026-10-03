import { describe, expect, it } from "vitest";

import {
  DIRTY_WARNING,
  HOLDOUT_COUNT,
  compareSelection,
  compareSeries,
  compareWarningLines,
  formFromRun,
  jumpWindow,
  sortTrades,
  warningLines,
  type RunConfig,
  type TradeRow,
} from "./present";

const config = (): RunConfig => ({
  strategy: "opening_range_breakout",
  params: { range_minutes: 15, lots: 1 },
  symbol: "NIFTY50",
  timeframe: "5m",
  start: "2024-10-03",
  end: null,
  sessions: ["normal", "weekend_full"],
  mode: "options",
  strike_offset: 0,
  slippage_points: 0.5,
});

describe("formFromRun", () => {
  it("opens a duplicate the user can edit without changing the saved config", () => {
    const saved = config();
    const form = formFromRun(saved);
    form.slippage_points = 1.5;
    form.params.range_minutes = 30;
    form.sessions.push("muhurat");
    expect(saved.slippage_points).toBe(0.5);
    expect(saved.params.range_minutes).toBe(15);
    expect(saved.sessions).toEqual(["normal", "weekend_full"]);
    expect(form.strategy).toBe("opening_range_breakout");
  });
});

describe("warnings", () => {
  it("shows the dirty-tree sentence on a result and in compare", () => {
    expect(warningLines(["strike step is UNVERIFIED"], true)).toEqual([
      DIRTY_WARNING,
      "strike step is UNVERIFIED",
    ]);
    expect(warningLines([DIRTY_WARNING], true)).toEqual([DIRTY_WARNING]);
    expect(warningLines(["strike step is UNVERIFIED"], false)).toEqual(["strike step is UNVERIFIED"]);
    const lines = compareWarningLines([
      { label: "ORB 0.5", git_dirty: true, warnings: [] },
      { label: "ORB 1.0", git_dirty: false, warnings: ["cost rates are UNVERIFIED"] },
      { label: "ORB 1.5", git_dirty: true, warnings: [] },
    ]);
    expect(lines[0]).toBe(DIRTY_WARNING);
    expect(lines.some((line) => line.includes("ORB 0.5") && line.includes(DIRTY_WARNING))).toBe(true);
    expect(lines.some((line) => line.includes("ORB 1.5") && line.includes(DIRTY_WARNING))).toBe(true);
    expect(lines.some((line) => line.includes("ORB 1.0") && line.includes(DIRTY_WARNING))).toBe(false);
  });
});

describe("sortTrades", () => {
  const rows: TradeRow[] = [
    { id: 1, entry_time: 30, exit_time: 40, direction: "SHORT", net_pnl: -5 },
    { id: 2, entry_time: 10, exit_time: 50, direction: "LONG", net_pnl: 12 },
    { id: 3, entry_time: 20, exit_time: 25, direction: "LONG", net_pnl: -5 },
  ];

  it("sorts by entry, exit, and net, and keeps the original order on a tie", () => {
    expect(sortTrades(rows, "entry_time", "asc").map((r) => r.id)).toEqual([2, 3, 1]);
    expect(sortTrades(rows, "exit_time", "desc").map((r) => r.id)).toEqual([2, 1, 3]);
    expect(sortTrades(rows, "net_pnl", "asc").map((r) => r.id)).toEqual([1, 3, 2]);
    expect(sortTrades(rows, "direction", "asc").map((r) => r.id)).toEqual([2, 3, 1]);
  });
});

describe("jumpWindow", () => {
  const times = Array.from({ length: 200 }, (_, i) => 1_000 + i * 300);

  it("places the entry a third of the way across, or asks for a load when the bar is absent", () => {
    const placed = jumpWindow(times, times[90]!, 120);
    expect(placed.kind).toBe("range");
    if (placed.kind !== "range") return;
    expect(placed.to - placed.from).toBe(120);
    expect((90 - placed.from) / (placed.to - placed.from)).toBeCloseTo(1 / 3, 5);
    expect(jumpWindow(times, 50, 120)).toEqual({ kind: "load", center: 50 });
  });
});

describe("walk-forward results", () => {
  it("shows the combination warning and the fixed holdout count", () => {
    expect(warningLines(["walk-forward tried 4 combinations"], false)).toContain(
      "walk-forward tried 4 combinations",
    );
    expect(HOLDOUT_COUNT(2)).toBe("final holdout has been run 2 times");
  });

  it("compare plots the stitched out-of-sample equity", () => {
    const series = compareSeries({
      kind: "walk_forward",
      equity: {
        option: [
          { exit_time: 1, equity: 30, drawdown: 0 },
          { exit_time: 2, equity: 20, drawdown: 10 },
          { exit_time: 3, equity: 25, drawdown: 10 },
        ],
      },
    });
    expect(series.map((point) => point.equity)).toEqual([30, 20, 25]);
  });
});

describe("compareSelection", () => {
  it("accepts two or three distinct runs and rejects anything else", () => {
    expect(compareSelection(["a", "b"])).toEqual({ ok: true, ids: ["a", "b"] });
    expect(compareSelection(["a", "b", "c"])).toEqual({ ok: true, ids: ["a", "b", "c"] });
    expect(compareSelection(["a"]).ok).toBe(false);
    expect(compareSelection(["a", "b", "c", "d"]).ok).toBe(false);
    expect(compareSelection(["a", "a"]).ok).toBe(false);
  });
});
