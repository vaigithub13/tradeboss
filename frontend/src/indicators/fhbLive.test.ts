/**
 * Live-path regressions for First Hour Box: candles must keep updating with FHB on,
 * toggle off must clear client-only from the live view, and a silent socket reconnects.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createInstance } from "./catalog";
import { computeFirstHourBox, snapMarkersToCandles } from "./firstHourBox";
import { LiveClient, type SocketLike } from "../live/client";
import { mergeLiveCandles, tailChange } from "../live/merge";
import { liveView } from "../store/liveStore";
import type { Candle } from "../api/client";

const IST = 19_800;

function dayStart(y: number, m: number, d: number): number {
  return Date.UTC(y, m - 1, d, 0, 0, 0) / 1000 - IST;
}

function bar(time: number, close: number): Candle {
  return { time, open: close - 1, high: close + 2, low: close - 2, close, volume: 0, oi: null };
}

describe("FHB + live candle path", () => {
  it("live ticks across a 5m boundary append a new candle while FHB markers stay on-series", () => {
    const open = dayStart(2026, 11, 17) + (9 * 60 + 15) * 60; // 09:15
    // Build through locked box (A+B) then into status bars on 5m grid
    let candles: Candle[] = [];
    for (let t = open; t < open + 90 * 60; t += 5 * 60) {
      candles.push(bar(t, 24_400 + (t - open) / 60));
    }
    const beforeLen = candles.length;
    const last = candles[candles.length - 1]!;
    // New 5m bar (boundary)
    const next = bar(last.time + 5 * 60, last.close + 3);
    const merged = mergeLiveCandles(candles, [next]);
    expect(merged.changed).toBe(true);
    expect(merged.candles).toHaveLength(beforeLen + 1);
    expect(tailChange(candles, merged.candles)).not.toBeNull();

    const fhb = computeFirstHourBox(merged.candles, { asOf: next.time, maxWidth: 150 });
    expect(fhb.boxes.length).toBeGreaterThan(0);
    const times = merged.candles.map((c) => c.time);
    const snapped = snapMarkersToCandles(fhb.markers, times);
    expect(snapped.every((m) => times.includes(m.time))).toBe(true);
  });

  it("forming (before 10:15) and locked (after) both yield a box without breaking merge", () => {
    const open = dayStart(2026, 11, 17) + (9 * 60 + 15) * 60;
    const formingBars = [bar(open, 100), bar(open + 5 * 60, 101)];
    const forming = computeFirstHourBox(formingBars, { asOf: open + 5 * 60 });
    expect(forming.boxes[0]?.forming).toBe(true);

    const lockedBars = [
      bar(open, 100),
      ...Array.from({ length: 12 }, (_, i) => bar(open + (i + 1) * 5 * 60, 100 + i)),
    ];
    const locked = computeFirstHourBox(lockedBars, { asOf: open + 70 * 60 });
    expect(locked.boxes[0]?.forming).toBe(false);

    const tip = bar(open + 75 * 60, 120);
    const merged = mergeLiveCandles(lockedBars, [tip]);
    expect(merged.candles.at(-1)?.time).toBe(tip.time);
  });

  it("toggling FHB on/off keeps the live view valid (client-only stripped)", () => {
    const ema = createInstance("ema", []);
    const fhb = { ...createInstance("first_hour_box", []), visible: true };
    const on = liveView({ symbol: "NIFTY50", timeframe: "5m", sessions: ["normal"] }, [ema, fhb]);
    expect(on?.indicators.every((i) => i.type !== "first_hour_box")).toBe(true);
    const off = liveView({ symbol: "NIFTY50", timeframe: "5m", sessions: ["normal"] }, [
      ema,
      { ...fhb, visible: false },
    ]);
    expect(off?.indicators.map((i) => i.type)).toEqual(["ema"]);
  });
});

describe("silent stream reconnect", () => {
  class FakeSocket implements SocketLike {
    readyState = 0;
    sent: string[] = [];
    closed = false;
    onopen: (() => void) | null = null;
    onmessage: ((ev: { data: unknown }) => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    send(data: string): void {
      this.sent.push(data);
    }
    close(): void {
      this.closed = true;
      this.readyState = 3;
      this.onclose?.();
    }
    open(): void {
      this.readyState = 1;
      this.onopen?.();
    }
  }

  let now = 0;
  let timers: { id: number; at: number; fn: () => void }[] = [];
  let tid = 1;
  let sockets: FakeSocket[] = [];

  beforeEach(() => {
    now = 0;
    timers = [];
    tid = 1;
    sockets = [];
  });
  afterEach(() => {
    timers = [];
  });

  function advance(ms: number): void {
    now += ms;
    const due = timers.filter((t) => t.at <= now).sort((a, b) => a.at - b.at);
    timers = timers.filter((t) => t.at > now);
    for (const t of due) t.fn();
  }

  it("watchdog closes a silent socket and opens another", () => {
    const client = new LiveClient(
      {
        url: "ws://test",
        makeSocket: () => {
          const s = new FakeSocket();
          sockets.push(s);
          return s;
        },
        timers: {
          setTimeout: (fn, ms) => {
            const id = tid++;
            timers.push({ id, at: now + ms, fn });
            return id;
          },
          clearTimeout: (id) => {
            timers = timers.filter((t) => t.id !== id);
          },
          now: () => now,
        },
        watchdogMs: 6000,
        backoffMs: [1000],
      },
      {
        onLink: () => {},
        onStatus: () => {},
        onBar: () => {},
        onReload: () => {},
      },
    );
    client.start();
    sockets[0]!.open();
    client.setView({ symbol: "NIFTY50", timeframe: "5m", sessions: ["normal"], indicators: [] });
    advance(6000);
    expect(sockets[0]!.closed).toBe(true);
    advance(1000);
    expect(sockets).toHaveLength(2);
  });
});
