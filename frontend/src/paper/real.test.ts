import { describe, expect, it } from "vitest";

import {
  accountStopProgress,
  compareReal,
  fromDatetimeLocalValue,
  orderHelperFromOpen,
  paperTradeKey,
  realNet,
  toDatetimeLocalValue,
} from "./real";

describe("real trades helpers", () => {
  it("keys a paper trade and nets a long option", () => {
    expect(paperTradeKey("3", "2026-11-11", 1794380160)).toBe("3:2026-11-11:1794380160");
    expect(realNet(212.65, 296.15, 65, 90.37)).toBeCloseTo(5337.13, 2);
  });

  it("compares real vs paper on price, delay and net", () => {
    const c = compareReal(
      { entry_premium: 212.65, exit_premium: 296.15, net: 5337.13, fill_time: 1000, exit_time: 2000, units: 65 },
      { entry_price: 213, exit_price: 295, charges: 100, entry_time: 1005, exit_time: 2010, units: 65 },
    );
    expect(c.entry_price_diff).toBeCloseTo(0.35);
    expect(c.exit_price_diff).toBeCloseTo(-1.15);
    expect(c.entry_delay_s).toBe(5);
    expect(c.exit_delay_s).toBe(10);
    expect(c.net).toBe(realNet(213, 295, 65, 100));
  });

  it("builds the order helper from an open premium position", () => {
    expect(
      orderHelperFromOpen({
        symbol: "NIFTY 25300 PE 17 NOV 26",
        units: 65,
        lots: 1,
        lot_size: 65,
        entry: { price: 212.65 },
        levels: { premium_stop: 170.12, premium_target: 297.71 },
      }),
    ).toEqual({
      contract: "NIFTY 25300 PE 17 NOV 26",
      quantity: 65,
      lots: 1,
      lot_size: 65,
      side: "BUY",
      entry_premium: 212.65,
      stop_trigger: 170.12,
      stop_limit: 170.07,
      target_limit: 297.71,
    });
    expect(orderHelperFromOpen(null)).toBeNull();
    expect(orderHelperFromOpen({ symbol: "X", units: 65, levels: {} })).toBeNull();
  });

  it("marks the account stop when loss reaches the limit", () => {
    expect(accountStopProgress(-1200, 5000).reached).toBe(false);
    expect(accountStopProgress(-1200, 5000).fraction).toBeCloseTo(0.24);
    expect(accountStopProgress(-5000, 5000).reached).toBe(true);
    expect(accountStopProgress(800, 5000).loss_used).toBe(0);
  });

  it("round-trips IST datetime-local values", () => {
    // 2026-11-11 12:26:00 IST
    const unix = fromDatetimeLocalValue("2026-11-11T12:26:00");
    expect(unix).toBe(1794380160);
    expect(toDatetimeLocalValue(unix!)).toBe("2026-11-11T12:26:00");
    expect(fromDatetimeLocalValue("nope")).toBeNull();
  });
});
