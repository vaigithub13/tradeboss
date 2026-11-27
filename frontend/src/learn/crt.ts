/**
 * Candle Range Theory for the Learn tab. Education only.
 *
 * Candle 1 sets a range (its high, low and 50%). Candle 2 trades beyond ONE
 * side and CLOSES back inside → CRT ✓, target = the other side. A close
 * outside is a breakout, and taking both sides is an outside bar: no CRT.
 *
 * Two timeframes, built from closed 5-minute candles in IST:
 * - 75m candles anchored at 09:15 (09:15, 10:30, 11:45, 13:00, 14:15).
 *   Candle 1 and candle 2 sit in the same session.
 * - Daily candles (09:15–15:30). Candle 2 is known at 15:30, so the daily
 *   target is for the next session.
 *
 * Nothing here is known before the 5m candle that shows it has closed.
 */

import { isHoliday } from "./events";
import type { LearnBar } from "./structure";
import { istClock, istEpoch } from "./time";

export type CrtTf = "75m" | "D1";
export type CrtStatus = "pending" | "forming" | "crt" | "breakout" | "outside" | "none";

const M5 = 300;
const OPEN = 9 * 60 + 15;
const CLOSE = 15 * 60 + 30;
export const SLOT = 75 * 60;

export interface CrtCandle {
  t: number;
  end: number;
  day: string;
  open: number;
  high: number;
  low: number;
  close: number;
  complete: boolean;
  bars: LearnBar[];
}

export interface CrtPair {
  tf: CrtTf;
  c1: CrtCandle;
  c2: CrtCandle;
  hi: number;
  lo: number;
  mid: number;
  status: CrtStatus;
  swept: "high" | "low" | "both" | null;
  /** Close of the 5m candle that first traded beyond candle 1. */
  sweepAt: number | null;
  ext: number | null;
  bias: "up" | "down" | null;
  target: number | null;
  close2: number;
  /** End of the window the target is watched in: candle 3 (75m) or the next session (D1). */
  c3End: number;
  hitAt: number | null;
}

function dayOpen(day: string): number {
  const [year, month, date] = day.split("-").map(Number);
  return istEpoch(year ?? 1970, month ?? 1, date ?? 1, 9, 15);
}

/** Start of the 75m slot holding a 5m bar, or null outside 09:15–15:30. */
export function crtSlot(epoch: number): number | null {
  const clock = istClock(epoch);
  if (clock.minutes < OPEN || clock.minutes >= CLOSE) return null;
  const offset = clock.minutes - OPEN;
  return dayOpen(clock.day) + Math.floor(offset / 75) * SLOT;
}

/** The trading session after `day`: weekends and NSE holidays are skipped. */
export function nextSession(day: string): string {
  let at = dayOpen(day);
  for (let step = 0; step < 15; step += 1) {
    at += 86400;
    const clock = istClock(at);
    if (clock.weekday !== 0 && clock.weekday !== 6 && !isHoliday(clock.day)) return clock.day;
  }
  return istClock(at).day;
}

/** Higher-timeframe candles from 5m bars that have closed by `now`. */
export function crtCandles(bars: readonly LearnBar[], now: number, tf: CrtTf): CrtCandle[] {
  const out: CrtCandle[] = [];
  let current: CrtCandle | null = null;
  for (const bar of bars) {
    if (bar.time + M5 > now) break;
    const clock = istClock(bar.time);
    if (clock.minutes < OPEN || clock.minutes >= CLOSE) continue;
    const start: number = tf === "75m" ? crtSlot(bar.time)! : dayOpen(clock.day);
    if (!current || current.t !== start) {
      const end: number = tf === "75m" ? start + SLOT : start + (CLOSE - OPEN) * 60;
      current = {
        t: start, end, day: clock.day, open: bar.open ?? bar.close, high: bar.high, low: bar.low, close: bar.close,
        complete: false, bars: [],
      };
      out.push(current);
    }
    current.high = Math.max(current.high, bar.high);
    current.low = Math.min(current.low, bar.low);
    current.close = bar.close;
    current.bars.push(bar);
  }
  for (const candle of out) candle.complete = now >= candle.end;
  return out;
}

function emptyCandle(t: number, tf: CrtTf, day: string): CrtCandle {
  const end = tf === "75m" ? t + SLOT : t + (CLOSE - OPEN) * 60;
  return { t, end, day, open: 0, high: 0, low: 0, close: 0, complete: false, bars: [] };
}

/** The live candle 2 that has started but has no closed 5m bar yet. */
function startedNext(last: CrtCandle, now: number, tf: CrtTf): CrtCandle | null {
  if (!last.complete) return null;
  if (tf === "75m") {
    const next = last.end;
    if (now < next || istClock(next).minutes >= CLOSE || istClock(next).day !== last.day) return null;
    if (now >= next + SLOT) return null;
    return emptyCandle(next, tf, last.day);
  }
  const day = nextSession(last.day);
  const open = dayOpen(day);
  if (now < open || now >= open + (CLOSE - OPEN) * 60) return null;
  return emptyCandle(open, tf, day);
}

function readPair(tf: CrtTf, c1: CrtCandle, c2: CrtCandle, after: readonly LearnBar[], now: number): CrtPair {
  const hi = c1.high;
  const lo = c1.low;
  const up = c2.bars.find((bar) => bar.high > hi) ?? null;
  const down = c2.bars.find((bar) => bar.low < lo) ?? null;
  const c3End = tf === "75m" ? Math.min(c2.end + SLOT, dayOpen(c2.day) + (CLOSE - OPEN) * 60) : dayOpen(nextSession(c2.day)) + (CLOSE - OPEN) * 60;
  const pair: CrtPair = {
    tf, c1, c2, hi, lo, mid: (hi + lo) / 2, status: "none", swept: null, sweepAt: null, ext: null,
    bias: null, target: null, close2: c2.close, c3End, hitAt: null,
  };
  if (up && down) {
    pair.swept = "both";
    pair.status = "outside";
    pair.sweepAt = Math.max(up.time, down.time) + M5;
    return pair;
  }
  const hit = up ?? down;
  if (!hit) {
    pair.status = c2.complete ? "none" : "pending";
    return pair;
  }
  const high = hit === up;
  pair.swept = high ? "high" : "low";
  pair.sweepAt = hit.time + M5;
  pair.ext = high ? Math.max(...c2.bars.map((bar) => bar.high)) : Math.min(...c2.bars.map((bar) => bar.low));
  if (!c2.complete) {
    pair.status = "forming";
    return pair;
  }
  if (c2.close > lo && c2.close < hi) {
    pair.status = "crt";
    pair.bias = high ? "down" : "up";
    pair.target = high ? lo : hi;
    const reach = after.find((bar) => bar.time >= c2.end && bar.time + M5 <= Math.min(now, c3End)
      && (high ? bar.low <= lo : bar.high >= hi));
    pair.hitAt = reach ? reach.time + M5 : null;
  } else {
    pair.status = "breakout";
  }
  return pair;
}

/**
 * Every candle-1 / candle-2 pair read at `now`. A 75m pair stays inside one
 * session. The last pair can be the live one (pending or forming).
 */
export function crtPairs(bars: readonly LearnBar[], now: number, tf: CrtTf): CrtPair[] {
  const candles = crtCandles(bars, now, tf);
  const closed = bars.filter((bar) => bar.time + M5 <= now);
  const out: CrtPair[] = [];
  let cursor = 0;
  for (let k = 0; k + 1 < candles.length; k += 1) {
    const c1 = candles[k]!;
    const c2 = candles[k + 1]!;
    if (!c1.complete) continue;
    if (tf === "75m" && (c1.day !== c2.day || c2.t !== c1.end)) continue;
    while (cursor < closed.length && closed[cursor]!.time < c2.end) cursor += 1;
    out.push(readPair(tf, c1, c2, closed.slice(cursor, cursor + 120), now));
  }
  const last = candles.at(-1);
  const next = last ? startedNext(last, now, tf) : null;
  if (last && next) out.push(readPair(tf, last, next, [], now));
  return out;
}

export function crtName(tf: CrtTf): string {
  return tf === "75m" ? "75m CRT" : "D1 CRT";
}
