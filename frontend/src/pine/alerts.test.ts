import { describe, expect, it } from "vitest";

import { firePineAlerts, pineNotification, resetPineAlertMemory, signalsToAlertRows } from "./alerts";
import { usePineLiveStore } from "../store/pineLiveStore";

describe("pine alerts", () => {
  it("dedupes the same bar and still writes the Alerts log", () => {
    resetPineAlertMemory();
    usePineLiveStore.setState({ log: [], soundOn: false });
    const rows = signalsToAlertRows(
      { id: "s1", name: "EMA", symbol: "NIFTY50", timeframe: "5m" },
      [{ time: 100, side: "BUY", text: "BUY CE", price: 24000, source: "plotshape" }],
    );
    expect(firePineAlerts("s1", "EMA", false, rows, { playSound: false })).toHaveLength(1);
    expect(firePineAlerts("s1", "EMA", false, rows, { playSound: false })).toHaveLength(0);
    expect(usePineLiveStore.getState().log).toHaveLength(1);
  });

  it("builds a browser notification title/body", () => {
    const n = pineNotification({
      id: "1",
      time: 100,
      scriptId: "s",
      scriptName: "EMA",
      symbol: "SENSEX",
      timeframe: "5m",
      side: "SELL",
      text: "BUY PE",
      price: 82000.5,
      day: "2026-11-17",
    });
    expect(n.title).toMatch(/SELL/);
    expect(n.body).toMatch(/SENSEX/);
  });
});
