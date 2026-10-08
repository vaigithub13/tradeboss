import { describe, expect, it } from "vitest";

import { exitCountsLine, istTime, reportCells, type ReportRow } from "./present";

// 8 Oct 2026 09:24:37 IST and friends
const T = (h: number, m: number, s = 0) => Date.UTC(2026, 9, 8, h - 5, m - 30, s) / 1000;

const row: ReportRow = {
  direction: "SHORT", signal_time: T(9, 15), trigger_index: 22521.05, fill_time: T(9, 24, 37),
  contract: "NIFTY 22500 PE 13 OCT 26", entry_premium: 126.25, entry_source: "real", units: 65,
  rule: "premium -20% / +40%", index_stop: 22571.95, index_target: 22419.45, premium_stop: 101, premium_target: 176.75,
  estimated: ["index"], exit_time: T(10, 40, 49), exit_premium: 175.5, exit_reason: "target",
  exit_reason_raw: "premium_target", net: 3128.44, risk: 1641.25, r_multiple: 1.906,
};

describe("trade report", () => {
  it("formats a paper row in IST with the estimated side marked", () => {
    const c = reportCells(row);
    expect(c.signal).toBe("09:15:00");
    expect(c.fill).toBe("09:24:37");
    expect(c.contract).toBe("22500 PE");
    expect(c.entry).toBe("126.25 real");
    expect(c.indexLevels).toBe("~22571.95 / 22419.45");
    expect(c.premiumLevels).toBe("101.00 / 176.75");
    expect(c.exit).toBe("10:40:49");
    expect(c.reason).toBe("target");
    expect(c.r).toBe("+1.91R");
  });

  it("shows the date for a backtest and dashes where there is no rule", () => {
    const bare = { ...row, rule: null, index_stop: null, index_target: null, premium_stop: null, premium_target: null,
      estimated: [], r_multiple: null, risk: null };
    const c = reportCells(bare, true);
    expect(c.fill).toBe("2026-10-08 09:24:37");
    expect(c.indexLevels).toBe("—");
    expect(c.r).toBe("—");
    expect(reportCells({ ...row, r_multiple: -1.04 }).r).toBe("-1.04R");
    expect(istTime(null)).toBe("—");
  });

  it("lists the exits by kind", () => {
    expect(exitCountsLine({ target: 1, stop: 2, reversal: 1, "square-off": 3, other: 0 }))
      .toBe("target 1 · stop 2 · reversal 1 · square-off 3 · other 0");
    expect(exitCountsLine(null)).toBe("target 0 · stop 0 · reversal 0 · square-off 0 · other 0");
  });
});
