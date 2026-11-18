import { describe, expect, it } from "vitest";

import { prepareOhlcv, rewriteForRuntime, scanCompat, toPineTimeframe } from "./compat";

describe("pine compat", () => {
  it("flags unsupported calls with line numbers", () => {
    const src = `//@version=6\nindicator("t")\nrequest.security_lower_tf(syminfo.tickerid, "1", close)\n`;
    const r = scanCompat(src);
    expect(r.unsupported[0]?.message).toMatch(/line 3: request\.security_lower_tf not supported/);
  });

  it("warns may repaint for lookahead_on without [1]", () => {
    const bad = `x = request.security(syminfo.tickerid, "5", close, lookahead=barmerge.lookahead_on)`;
    const good = `x = request.security(syminfo.tickerid, "5", close[1], lookahead=barmerge.lookahead_on)`;
    expect(scanCompat(bad).repaint.length).toBeGreaterThan(0);
    expect(scanCompat(good).repaint).toHaveLength(0);
  });

  it("warns on barstate.isrealtime and varip", () => {
    expect(scanCompat("a = barstate.isrealtime").repaint[0]?.reason).toMatch(/may repaint/);
    expect(scanCompat("varip float x = 0").repaint[0]?.reason).toMatch(/varip/);
  });

  it("rewrites syminfo.tickerid for PineTS array data", () => {
    expect(rewriteForRuntime("request.security(syminfo.tickerid, \"5\", close)", "NIFTY50")).toContain(
      '"NIFTY50"',
    );
  });

  it("injects futures volume for index VWAP and notices when missing", () => {
    const candles = [
      { time: 1, open: 1, high: 2, low: 0.5, close: 1.5, volume: 0 },
      { time: 2, open: 1.5, high: 2, low: 1, close: 1.8, volume: 0 },
    ];
    const missing = prepareOhlcv(candles, null);
    expect(missing.vwapNotice).toMatch(/unavailable/);
    const ok = prepareOhlcv(candles, [100, 200]);
    expect(ok.bars[0]?.volume).toBe(100);
    expect(ok.vwapNotice).toMatch(/futures volume/);
  });

  it("maps TradeBoss timeframes to Pine", () => {
    expect(toPineTimeframe("5m")).toBe("5");
    expect(toPineTimeframe("1h")).toBe("60");
  });
});
