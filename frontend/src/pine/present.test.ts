import { describe, expect, it } from "vitest";

import {
  approveEnabled,
  cardLines,
  convertEnabled,
  disabledReasons,
  replaceAsked,
  runLiveEnabled,
  scriptTitle,
  showBacktestCard,
  type PineCard,
} from "./present";

describe("pine panel gates", () => {
  it("keeps conversion disabled until the report is accepted", () => {
    expect(convertEnabled(false)).toBe(false);
    expect(convertEnabled(true)).toBe(true);
  });

  it("hides Approve when the draft failed the check", () => {
    expect(approveEnabled(false)).toBe(false);
    expect(approveEnabled(true)).toBe(true);
  });

  it("shows why a disabled button cannot be used", () => {
    const open = { busy: false, hasReport: true, accepted: true, ready: true, approved: false, errors: [] };
    expect(disabledReasons("approve", open)).toEqual([]);
    expect(disabledReasons("approve", { ...open, busy: true })).toEqual(["The check is still running."]);
    expect(disabledReasons("approve", { ...open, ready: false, errors: ["remove line 3: from typing import Any"] })).toEqual([
      "remove line 3: from typing import Any",
    ]);
    expect(disabledReasons("approve", { ...open, ready: false, errors: [] })).toEqual(["The draft failed the checks."]);
    expect(disabledReasons("convert", { ...open, accepted: false })).toEqual([
      "Accept the semantics report before converting.",
    ]);
    expect(disabledReasons("accept", { ...open, hasReport: false })).toEqual([
      "Run the semantics report before accepting.",
    ]);
    expect(disabledReasons("report", { ...open, busy: true })).toEqual(["The semantics report is still running."]);
  });

  it("asks before replacing a strategy that is already saved", () => {
    expect(replaceAsked(409)).toBe(true);
    expect(replaceAsked(400)).toBe(false);
  });

  it("hides the backtest card for an indicator script", () => {
    expect(showBacktestCard("indicator")).toBe(false);
    expect(showBacktestCard("strategy")).toBe(true);
  });

  it("gates Run live on accept, unsupported calls, and the 5-script cap", () => {
    expect(runLiveEnabled(false, 0, 0)).toBe(false);
    expect(runLiveEnabled(true, 1, 0)).toBe(false);
    expect(runLiveEnabled(true, 0, 5)).toBe(false);
    expect(runLiveEnabled(true, 0, 4)).toBe(true);
    const open = {
      busy: false,
      hasReport: true,
      accepted: true,
      ready: true,
      approved: false,
      errors: [],
      scriptCount: 5,
      unsupported: [] as string[],
    };
    expect(disabledReasons("runLive", open)[0]).toMatch(/Max 5/);
    expect(scriptTitle('strategy("NIFTY EMA 9-21 PRO Intraday v6", overlay=true)')).toBe(
      "NIFTY EMA 9-21 PRO Intraday v6",
    );
  });

  it("renders the summary warnings and the overnight split", () => {
    const card: PineCard = {
      warnings: [
        "cost rates are UNVERIFIED",
        "holdout not run",
        "unverified: stop=na behaviour on TradingView not reproduced",
      ],
      option_fill: "delta_adjusted",
      overnight_net: 12.5,
      same_day_net: -3,
      checks: ["look_ahead", "determinism", "tv_parity", "realistic"],
    };
    const lines = cardLines(card);
    expect(lines.some((line) => line.includes("UNVERIFIED"))).toBe(true);
    expect(lines.some((line) => line.includes("holdout not run"))).toBe(true);
    expect(lines.some((line) => line.includes("delta_adjusted"))).toBe(true);
    expect(lines.some((line) => line.includes("12.5") && line.includes("-3"))).toBe(true);
  });
});
