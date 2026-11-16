import { describe, expect, it } from "vitest";

import {
  computeKpis,
  dailyPnLMatrix,
  equitySeries,
  filterDashboardRows,
  holdMinutes,
  livePositionView,
  maxDrawdown,
  slotStats,
  todaySummaryFromSlots,
  type DashboardFilters,
} from "./dashboard";
import type { PaperTradeRow } from "./trades";

function row(partial: Partial<PaperTradeRow> & Pick<PaperTradeRow, "slot" | "date" | "net" | "entry_time">): PaperTradeRow {
  return {
    direction: "CE",
    side: "LONG",
    signal_time: null,
    trigger_index: null,
    fill_time: partial.entry_time,
    contract: "NIFTY 25000 CE 16 NOV 26",
    entry_premium: 100,
    entry_source: "quote",
    units: 65,
    rule: null,
    index_stop: null,
    index_target: null,
    premium_stop: 80,
    premium_target: 140,
    estimated: [],
    exit_time: (partial.exit_time as number | undefined) ?? partial.entry_time + 600,
    exit_premium: 110,
    exit_reason: "target",
    exit_reason_raw: "target",
    risk: null,
    r_multiple: null,
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
    ...(partial.direction ? { direction: partial.direction } : {}),
    ...(partial.side ? { side: partial.side } : {}),
    ...(partial.exit_reason ? { exit_reason: partial.exit_reason } : {}),
    ...(partial.exit_time != null ? { exit_time: partial.exit_time } : {}),
  };
}

describe("dashboard helpers", () => {
  it("filters by slot, date, side, win/loss", () => {
    const rows = [
      row({ slot: "2", date: "2026-11-10", net: 100, entry_time: 1, direction: "CE" }),
      row({ slot: "4", date: "2026-11-11", net: -50, entry_time: 2, direction: "PE", side: "SHORT" }),
      row({ slot: "5", date: "2026-11-12", net: 200, entry_time: 3, direction: "CE" }),
    ];
    const f: DashboardFilters = { slot: "5", from: "2026-11-01", to: "2026-11-30", side: "CE", outcome: "win" };
    expect(filterDashboardRows(rows, f).map((r) => r.slot)).toEqual(["5"]);
    expect(filterDashboardRows(rows, { ...f, slot: "", side: "", outcome: "loss" }).map((r) => r.slot)).toEqual(["4"]);
  });

  it("computes KPIs and excludes retired slots from forward totals", () => {
    const rows = [
      row({ slot: "1", date: "2026-11-16", net: 9999, entry_time: 1 }),
      row({ slot: "2", date: "2026-11-16", net: 100, entry_time: 2 }),
      row({ slot: "4", date: "2026-11-16", net: -40, entry_time: 3 }),
      row({ slot: "5", date: "2026-11-10", net: 50, entry_time: 4 }),
    ];
    const k = computeKpis(rows, { today: "2026-11-16" });
    expect(k.tradesToday).toBe(2);
    expect(k.todayNet).toBe(60);
    expect(k.forwardNet).toBe(110);
    expect(k.winPct).toBeCloseTo(66.67, 1);
  });

  it("builds daily matrix and equity for active slots only", () => {
    const rows = [
      row({ slot: "2", date: "2026-11-14", net: 10, entry_time: 10, exit_time: 20 }),
      row({ slot: "2", date: "2026-11-15", net: 20, entry_time: 30, exit_time: 40 }),
      row({ slot: "1", date: "2026-11-15", net: 999, entry_time: 35, exit_time: 45 }),
    ];
    const daily = dailyPnLMatrix(rows);
    expect(daily[0]?.date).toBe("2026-11-15");
    expect(daily[0]?.bySlot["2"]).toBe(20);
    expect(daily[0]?.bySlot["1"]).toBeUndefined();
    const eq = equitySeries(rows);
    expect(eq.at(-1)?.bySlot["2"]).toBe(30);
  });

  it("merges same-second exits so equity times are unique and ascending (LWC requirement)", () => {
    const rows = [
      row({ slot: "2", date: "2026-11-11", net: -200, entry_time: 100, exit_time: 500 }),
      row({ slot: "4", date: "2026-11-11", net: 300, entry_time: 110, exit_time: 500 }),
      row({ slot: "5", date: "2026-11-11", net: 50, entry_time: 120, exit_time: 500 }),
      row({ slot: "2", date: "2026-11-12", net: 10, entry_time: 200, exit_time: 600 }),
    ];
    const eq = equitySeries(rows);
    const times = eq.map((p) => p.time);
    expect(times).toEqual([...times].sort((a, b) => a - b));
    expect(new Set(times).size).toBe(times.length);
    expect(eq).toHaveLength(2);
    expect(eq[0]?.bySlot["2"]).toBe(-200);
    expect(eq[0]?.bySlot["4"]).toBe(300);
    expect(eq[0]?.bySlot["5"]).toBe(50);
    expect(eq[1]?.bySlot["2"]).toBe(-190);
  });

  it("skips rows with invalid times and nullish nets when building equity", () => {
    const rows = [
      row({ slot: "2", date: "2026-11-11", net: 10, entry_time: Number.NaN, exit_time: Number.NaN }),
      row({ slot: "2", date: "2026-11-11", net: null as unknown as number, entry_time: 10, exit_time: 20 }),
      row({ slot: "4", date: "2026-11-11", net: 5, entry_time: 15, exit_time: 25 }),
    ];
    const eq = equitySeries(rows);
    expect(eq.every((p) => Number.isFinite(p.time))).toBe(true);
    expect(new Set(eq.map((p) => p.time)).size).toBe(eq.length);
  });

  it("maxDrawdown and holdMinutes", () => {
    expect(maxDrawdown([100, -50, -80, 20])).toBe(130);
    expect(holdMinutes(row({ slot: "2", date: "2026-11-16", net: 1, entry_time: 1000, exit_time: 1600 }))).toBe(10);
  });

  it("slotStats and today summary skip inactive in summary", () => {
    const rows = [
      row({ slot: "5", date: "2026-11-16", net: 100, entry_time: 1, exit_reason: "target" }),
      row({ slot: "5", date: "2026-11-15", net: -30, entry_time: 2, exit_reason: "stop" }),
    ];
    const s = slotStats(rows, "5", "2026-11-16");
    expect(s.todayNet).toBe(100);
    expect(s.totalNet).toBe(70);
    expect(s.exits.target).toBe(1);
    expect(s.avgWin).toBe(100);
    expect(s.avgLoss).toBe(-30);

    const summary = todaySummaryFromSlots([
      { slot: "1", summary: { trades: 9, wins: 9, net: 9000, modelled_legs: 0 } } as never,
      { slot: "2", summary: { trades: 2, wins: 1, net: 50, modelled_legs: 0 } } as never,
    ]);
    expect(summary.trades).toBe(2);
    expect(summary.net).toBe(50);
  });

  it("livePositionView exposes stop/target and null LTP", () => {
    const view = livePositionView({
      slot: "5",
      state: "running",
      position: {
        symbol: "NIFTY 25200 CE 17 NOV 26",
        entry: { price: 200 },
        levels: { premium_stop: 180, premium_target: null },
      },
      mark: { symbol: "NIFTY 25200 CE 17 NOV 26", source: "quote", gross: -50, net: -80 },
    } as never);
    expect(view?.side).toBe("CE");
    expect(view?.strike).toBe("25200");
    expect(view?.entry).toBe(200);
    expect(view?.ltp).toBeNull();
    expect(view?.unrealised).toBe(-80);
    expect(view?.stop).toBe(180);
  });
});
