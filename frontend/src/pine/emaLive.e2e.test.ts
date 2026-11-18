/**
 * End-to-end: NIFTY EMA 9-21 PRO fixture on synthetic closed candles.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { firePineAlerts, resetPineAlertMemory, signalsToAlertRows } from "./alerts";
import { scanCompat } from "./compat";
import { runPineScript } from "./runEngine";
import { usePineLiveStore } from "../store/pineLiveStore";

const FIXTURE = readFileSync(path.resolve(__dirname, "fixtures/nifty_ema_9_21_pro.pine"), "utf8");

function synthCandles(n: number): {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}[] {
  // 2026-11-17 09:15 IST — down, up, down again so both crossover and crossunder fire
  const t0 = Date.UTC(2026, 10, 17, 3, 45) / 1000;
  const out = [];
  for (let i = 0; i < n; i++) {
    let c: number;
    if (i < 60) c = 24100 - i * 3;
    else if (i < 140) c = 23920 + (i - 60) * 5;
    else c = 24320 - (i - 140) * 4;
    out.push({
      time: t0 + i * 60,
      open: c - 1,
      high: c + 8,
      low: c - 8,
      close: c,
      volume: 1000 + i,
    });
  }
  return out;
}

describe("NIFTY EMA 9-21 PRO live e2e", () => {
  it("runs end-to-end and produces BUY CE / BUY PE markers + alerts", async () => {
    const compat = scanCompat(FIXTURE);
    expect(compat.unsupported).toHaveLength(0);

    const closed = synthCandles(200);
    // Exclude forming tip — same as live host
    const result = await runPineScript({
      scriptId: "ema-test",
      source: FIXTURE,
      ticker: "NIFTY50",
      timeframe: "1m",
      candles: closed.slice(0, -1),
    });
    expect(result.error).toBeUndefined();
    const buys = result.signals.filter((s) => s.side === "BUY" || /BUY CE/i.test(s.text));
    const sells = result.signals.filter((s) => s.side === "SELL" || /BUY PE/i.test(s.text));
    expect(buys.length).toBeGreaterThan(0);
    expect(sells.length).toBeGreaterThan(0);

    resetPineAlertMemory();
    usePineLiveStore.setState({ log: [], soundOn: false });
    const rows = signalsToAlertRows(
      { id: "ema-test", name: "EMA", symbol: "NIFTY50", timeframe: "1m" },
      result.signals,
    );
    const first = firePineAlerts("ema-test", "EMA", false, rows, { playSound: false });
    const second = firePineAlerts("ema-test", "EMA", false, rows, { playSound: false });
    expect(first.length).toBeGreaterThan(0);
    expect(second).toHaveLength(0); // duplicate-alert guard
  }, 30_000);

  it("does not alert on the unclosed tip bar", async () => {
    const closed = synthCandles(120);
    const withTip = closed;
    const onlyClosed = closed.slice(0, -1);
    const tipTime = withTip[withTip.length - 1]!.time;

    const full = await runPineScript({
      scriptId: "tip",
      source: FIXTURE,
      ticker: "NIFTY50",
      timeframe: "1m",
      candles: onlyClosed,
    });
    expect(full.signals.every((s) => s.time !== tipTime)).toBe(true);
  }, 30_000);

  it("surfaces a script error without throwing", async () => {
    const result = await runPineScript({
      scriptId: "bad",
      source: `//@version=6\nstrategy("x")\nrequest.security_lower_tf(syminfo.tickerid, "1", close)\n`,
      ticker: "NIFTY50",
      timeframe: "1m",
      candles: synthCandles(30).slice(0, -1),
    });
    expect(result.error || scanCompat(result.error ?? "").unsupported).toBeTruthy();
  });
});
