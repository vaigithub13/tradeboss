import { describe, expect, it, vi } from "vitest";

import { closedTimeOnAppend, emitClosedBar, scheduleClosedBar, subscribeClosedBars } from "./closedBars";

describe("closed bars", () => {
  it("detects the previous tip when a newer bar appends", () => {
    const prev = [{ time: 100 }, { time: 200 }];
    const next = [{ time: 100 }, { time: 200 }, { time: 300 }];
    expect(closedTimeOnAppend(prev, next)).toBe(200);
    expect(closedTimeOnAppend(prev, prev)).toBeNull();
  });

  it("scheduleClosedBar fires after the current stack (microtask)", async () => {
    const order: string[] = [];
    const unsub = subscribeClosedBars(() => {
      order.push("pine");
    });
    order.push("update");
    scheduleClosedBar({
      scope: "nifty",
      symbol: "NIFTY50",
      timeframe: "5m",
      closedTime: 1,
      candles: [],
      history: false,
    });
    order.push("after-schedule");
    await Promise.resolve();
    expect(order).toEqual(["update", "after-schedule", "pine"]);
    unsub();
  });

  it("listener errors do not break other listeners", () => {
    const spy = vi.fn();
    const unsub1 = subscribeClosedBars(() => {
      throw new Error("boom");
    });
    const unsub2 = subscribeClosedBars(spy);
    emitClosedBar({
      scope: "sensex",
      symbol: "SENSEX",
      timeframe: "5m",
      closedTime: 1,
      candles: [],
      history: false,
    });
    expect(spy).toHaveBeenCalledOnce();
    unsub1();
    unsub2();
  });
});
