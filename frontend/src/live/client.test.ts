import { describe, expect, it } from "vitest";

import { LiveClient, type LinkState, type SocketLike, type Timers } from "./client";
import type { BarMessage, LiveStatus } from "./protocol";

class FakeSocket implements SocketLike {
  readyState = 0;
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.closed = true;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  push(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
  drop() {
    this.readyState = 3;
    this.onclose?.();
  }
}

/** Manual clock: timers fire only when `advance` passes their time. */
class Clock implements Timers {
  t = 1_000_000;
  private seq = 0;
  private timers = new Map<number, { at: number; fn: () => void }>();
  setTimeout = (fn: () => void, ms: number): unknown => {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + ms, fn });
    return id;
  };
  clearTimeout = (id: unknown): void => void this.timers.delete(id as number);
  now = (): number => this.t;
  advance(ms: number): void {
    const end = this.t + ms;
    for (;;) {
      const next = [...this.timers.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      this.timers.delete(next[0]);
      this.t = Math.max(this.t, next[1].at);
      next[1].fn();
    }
    this.t = end;
  }
}

const bar = (symbol: string, timeframe: string, close: number): BarMessage => ({
  type: "bar",
  symbol,
  timeframe,
  candles: [{ time: 100, open: 1, high: 2, low: 0, close, volume: 0, oi: null }],
  volume_known: true,
});

function setup(opts: { throttleMs?: number; watchdogMs?: number } = {}) {
  const clock = new Clock();
  const sockets: FakeSocket[] = [];
  const links: LinkState[] = [];
  const statuses: { status: LiveStatus; at: number }[] = [];
  const bars: BarMessage[] = [];
  const reloads: string[] = [];
  const client = new LiveClient(
    {
      url: "ws://x/api/live/ws",
      makeSocket: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s;
      },
      timers: clock,
      ...opts,
    },
    {
      onLink: (l) => links.push(l),
      onStatus: (status, at) => statuses.push({ status, at }),
      onBar: (m) => bars.push(m),
      onReload: (s) => reloads.push(s),
    },
  );
  return { clock, sockets, links, statuses, bars, reloads, client };
}

const VIEW = { symbol: "NIFTY50", timeframe: "5m", sessions: ["normal"], indicators: [] };

describe("LiveClient connection", () => {
  it("sends the current view when the socket opens, and again after a reconnect", () => {
    const { clock, sockets, client } = setup();
    client.setView(VIEW); // before any socket: remembered
    client.start();
    sockets[0]!.open();
    expect(JSON.parse(sockets[0]!.sent[0]!)).toMatchObject({ type: "view", symbol: "NIFTY50", timeframe: "5m" });

    sockets[0]!.drop();
    clock.advance(1000);
    expect(sockets).toHaveLength(2);
    sockets[1]!.open();
    expect(sockets[1]!.sent).toHaveLength(1);
  });

  it("a view change while open is sent at once", () => {
    const { sockets, client } = setup();
    client.start();
    sockets[0]!.open();
    client.setView(VIEW);
    client.setView({ ...VIEW, timeframe: "15m" });
    expect(sockets[0]!.sent.map((x) => JSON.parse(x).timeframe)).toEqual(["5m", "15m"]);
  });

  it("reconnects with growing, capped back-off, and resets it once a socket opened", () => {
    const { clock, sockets, client } = setup();
    client.start();
    for (const wait of [1000, 2000, 4000, 8000, 10000, 10000]) {
      const n = sockets.length;
      sockets.at(-1)!.drop();
      clock.advance(wait - 1);
      expect(sockets).toHaveLength(n); // not yet
      clock.advance(1);
      expect(sockets).toHaveLength(n + 1);
    }
    sockets.at(-1)!.open();
    sockets.at(-1)!.drop();
    clock.advance(1000);
    expect(sockets.length).toBe(8);
  });

  it("an error is treated like a close (one reconnect, not two)", () => {
    const { clock, sockets, client } = setup();
    client.start();
    sockets[0]!.onerror?.();
    sockets[0]!.onclose?.();
    clock.advance(1000);
    expect(sockets).toHaveLength(2);
  });

  it("closes and reopens a socket that went silent", () => {
    const { clock, sockets, links, client } = setup({ watchdogMs: 6000 });
    client.start();
    sockets[0]!.open();
    clock.advance(5000);
    sockets[0]!.push({ type: "status", state: "live", since_last_tick_s: 0, market: "open" });
    clock.advance(5000); // 5 s since the last message: still fine
    expect(sockets).toHaveLength(1);
    clock.advance(1500);
    expect(sockets[0]!.closed).toBe(true);
    expect(links.at(-1)).toBe("closed");
    clock.advance(1000);
    expect(sockets).toHaveLength(2);
  });

  it("stop() closes the socket and never reconnects", () => {
    const { clock, sockets, links, client } = setup();
    client.start();
    sockets[0]!.open();
    client.stop();
    expect(sockets[0]!.closed).toBe(true);
    expect(links.at(-1)).toBe("closed");
    clock.advance(60_000);
    expect(sockets).toHaveLength(1);
  });

  it("reports link states", () => {
    const { sockets, links, client } = setup();
    client.start();
    sockets[0]!.open();
    sockets[0]!.drop();
    expect(links).toEqual(["connecting", "open", "closed"]);
  });
});

describe("LiveClient messages", () => {
  it("passes status with the local arrival time", () => {
    const { clock, sockets, statuses, client } = setup();
    client.start();
    sockets[0]!.open();
    sockets[0]!.push({ type: "status", state: "stale", since_last_tick_s: 7, market: "open" });
    expect(statuses[0]).toEqual({ status: { state: "stale", since_last_tick_s: 7, market: "open" }, at: clock.t });
  });

  it("delivers the first bar immediately, then at most one batch per throttle interval with the NEWEST candles", () => {
    const { clock, sockets, bars, client } = setup({ throttleMs: 200 });
    client.start();
    sockets[0]!.open();
    sockets[0]!.push(bar("NIFTY50", "5m", 1));
    clock.advance(0);
    expect(bars.map((b) => b.candles[0]!.close)).toEqual([1]);

    sockets[0]!.push(bar("NIFTY50", "5m", 2));
    sockets[0]!.push(bar("NIFTY50", "5m", 3));
    sockets[0]!.push(bar("NIFTY50", "5m", 4));
    clock.advance(199);
    expect(bars).toHaveLength(1);
    clock.advance(1);
    expect(bars.map((b) => b.candles[0]!.close)).toEqual([1, 4]);
  });

  it("keeps different symbol/timeframe pairs apart and never exceeds ~5 batches a second", () => {
    const { clock, sockets, bars, client } = setup({ throttleMs: 200 });
    client.start();
    sockets[0]!.open();
    for (let i = 0; i < 100; i++) {
      sockets[0]!.push(bar("NIFTY50", "5m", i));
      sockets[0]!.push(bar("NIFTY50", "15m", i));
      clock.advance(10); // 100 pushes a second
    }
    expect(bars.length).toBeLessThanOrEqual(2 * 6);
    expect(new Set(bars.map((b) => b.timeframe))).toEqual(new Set(["5m", "15m"]));
    clock.advance(200);
    expect(bars.filter((b) => b.timeframe === "5m").at(-1)!.candles[0]!.close).toBe(99);
  });

  it("forwards reload messages and ignores garbage", () => {
    const { sockets, reloads, bars, client } = setup();
    client.start();
    sockets[0]!.open();
    sockets[0]!.push({ type: "reload", symbol: "RELIANCE" });
    sockets[0]!.onmessage?.({ data: "not json" });
    sockets[0]!.push({ type: "pong" });
    expect(reloads).toEqual(["RELIANCE"]);
    expect(bars).toHaveLength(0);
  });

  it("drops pending bars on stop", () => {
    const { clock, sockets, bars, client } = setup();
    client.start();
    sockets[0]!.open();
    sockets[0]!.push(bar("A", "5m", 1));
    sockets[0]!.push(bar("A", "5m", 2));
    client.stop();
    clock.advance(1000);
    expect(bars.length).toBeLessThanOrEqual(0);
  });
});
