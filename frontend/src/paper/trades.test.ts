import { describe, expect, it } from "vitest";

import { COLUMNS, cellText, filterRows, sortRows, toCsv, totals, tradeOverlay, type PaperTradeRow } from "./trades";

const T = (d: number, h: number, m: number, s = 0) => Date.UTC(2026, 9, d, h - 5, m - 30, s) / 1000;

function row(over: Partial<PaperTradeRow> = {}): PaperTradeRow {
  return {
    date: "2026-10-08", slot: "4", side: "SHORT", direction: "PE", signal_time: T(8, 9, 15), trigger_index: 22521.05,
    fill_time: T(8, 9, 24, 37), entry_time: T(8, 9, 24, 37), index_entry: 22521.05, contract: "NIFTY 22500 PE 13 OCT 26",
    lots: 1, lot_size: 65, units: 65, entry_premium: 126.25, entry_source: "real", rule: "premium -20% / +40%",
    index_stop: 22571.55, index_target: 22420.05, premium_stop: 101, premium_target: 176.75, estimated: ["index"],
    exit_time: T(8, 10, 40, 49), index_exit: 22420.1, exit_premium: 175.5, nifty_points: -100.95, premium_points: 49.25,
    gross: 3201.25, charges: 72.81, net: 3128.44, risk: 1641.25, r_multiple: 1.906, exit_reason: "target",
    exit_reason_raw: "premium_target", source: "live", index_from_candles: [],
    ...over,
  } as PaperTradeRow;
}

describe("paper trades view", () => {
  it("has the columns in the asked order", () => {
    expect(COLUMNS.map((c) => c.label)).toEqual([
      "Date", "Slot", "Direction", "Signal bar", "Trigger Nifty", "Entry time", "Nifty at entry", "Contract",
      "Lots x qty", "Entry premium", "Stop: Nifty / premium", "Target: Nifty / premium", "Exit time", "Nifty at exit",
      "Exit premium", "Nifty points", "Premium points", "Gross", "Charges", "Net", "R", "Exit reason",
    ]);
  });

  it("formats times in IST and prices to two decimals, marking estimated and candle-filled values", () => {
    const r = row();
    expect(COLUMNS.map((c) => cellText(r, c.key))).toEqual([
      "2026-10-08", "4", "PE", "09:15:00", "22521.05", "09:24:37", "22521.05", "22500 PE 13 OCT 26", "1 x 65",
      "126.25 real", "~22571.55 / 101.00", "~22420.05 / 176.75", "10:40:49", "22420.10", "175.50", "-100.95",
      "+49.25", "3201.25", "72.81", "3128.44", "+1.91", "target",
    ]);
    const old = row({ index_from_candles: ["exit"], index_stop: null, index_target: null, premium_stop: null,
      premium_target: null, estimated: [], r_multiple: null, entry_source: "modelled" });
    expect(cellText(old, "niftyExit")).toBe("≈22420.10");
    expect(cellText(old, "stop")).toBe("—");
    expect(cellText(old, "r")).toBe("—");
    expect(cellText(old, "entryPremium")).toBe("126.25 modelled");
  });

  it("sorts, filters and totals", () => {
    const rows = [row({ slot: "1", net: -500, r_multiple: null, exit_reason: "square-off", date: "2026-10-07" }),
      row(), row({ slot: "3", net: -1980.84, r_multiple: -1.02, exit_reason: "stop", date: "2026-10-09" })];
    expect(sortRows(rows, "net", "desc").map((r) => r.slot)).toEqual(["4", "1", "3"]);
    expect(sortRows(rows, "r", "asc").map((r) => r.slot)).toEqual(["3", "4", "1"]); // empty last
    expect(filterRows(rows, { slot: "4", from: "", to: "" }).map((r) => r.slot)).toEqual(["4"]);
    expect(filterRows(rows, { slot: "", from: "2026-10-08", to: "2026-10-08" }).map((r) => r.slot)).toEqual(["4"]);
    expect(totals(rows)).toEqual({
      trades: 3, wins: 1, net: 647.6, avgR: 0.443,
      exits: { target: 1, stop: 1, reversal: 0, "square-off": 1, other: 0 },
    });
  });

  it("exports CSV with the same columns", () => {
    const csv = toCsv([row({ contract: 'NIFTY "X", Y' })]).split("\n");
    expect(csv[0]!.startsWith("Date,Slot,Direction,Signal bar,")).toBe(true);
    expect(csv[1]!).toContain('"""X"", Y"');
    expect(csv[1]!.split(",")[0]).toBe("2026-10-08");
    expect(toCsv([row({ r_multiple: null })]).split("\n")[1]!.split(",").slice(-2)).toEqual(["", "target"]);
  });

  it("draws the trade like the position tool from the stored levels", () => {
    const o = tradeOverlay(row())!;
    expect(o.drawing.tool).toBe("short_position");
    expect(o.drawing.anchors).toEqual([
      { time: T(8, 9, 24, 37), price: 22521.05 },
      { time: T(8, 10, 40, 49), price: 22420.05 },
      { time: T(8, 9, 24, 37), price: 22571.55 },
    ]);
    expect(o.drawing.locked).toBe(true);
    expect(o.fixed).toMatchObject({ status: "target", endTime: T(8, 10, 40, 49), exitPrice: 22420.1, pnlRupees: 3128.44 });
    expect(o.fixed.centre[0]).toBe("Slot 4 22500 PE 13 OCT 26: target");
    expect(o.markers.map((m) => m.kind)).toEqual(["entry", "exit"]);
    // no levels: the zones run from the entry to the exit's Nifty level
    const bare = tradeOverlay(row({ side: "LONG", index_stop: null, index_target: null, index_exit: 22560, net: -10 }))!;
    expect(bare.drawing.tool).toBe("long_position");
    expect(bare.drawing.anchors.map((a) => a.price)).toEqual([22521.05, 22560, 22521.05]);
    expect(bare.fixed.status).toBe("stop");
    expect(tradeOverlay(row({ index_entry: null }))).toBeNull();
  });
});

describe("the drawn trade keeps its stored result", () => {
  it("shows the stored exit and net, not one computed from the bars", async () => {
    const { positionViews } = await import("../draw/positionFeed");
    const o = tradeOverlay(row())!;
    const view = positionViews([o.drawing], [], [], {}, {}, null, { [o.drawing.id]: o.fixed })[0]!;
    expect(view.outcome.status).toBe("target");
    expect(view.outcome.endTime).toBe(o.fixed.endTime);
    expect(view.outcome.pnlRupees).toBe(3128.44);
    expect(view.outcome.pnlPoints).toBeCloseTo(22521.05 - 22420.1, 6);
    expect(view.labels.find((l) => l.role === "centre")?.lines).toEqual(o.fixed.centre);
    const plain = positionViews([o.drawing], [], [], {}, {}, null)[0]!;
    expect(plain.outcome.status).not.toBe("target");
  });
});
