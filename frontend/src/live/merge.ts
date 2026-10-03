/** Pure helpers: fold live candles into the loaded window. */
import type { Candle } from "../api/client";
import { candleLowerBound } from "../indicators/cache";

export interface MergeResult {
  candles: Candle[];
  /** false when nothing changed (the same array is returned) */
  changed: boolean;
}

const same = (a: Candle, b: Candle): boolean =>
  a.time === b.time &&
  a.open === b.open &&
  a.high === b.high &&
  a.low === b.low &&
  a.close === b.close &&
  a.volume === b.volume &&
  a.oi === b.oi;

/**
 * Fold the newest candles (ascending) into `candles`: a candle with the last start time replaces
 * the last one, a newer one is appended, an older one replaces the candle with that start time
 * (if loaded). Untouched candles keep their object identity, so the chart can tell a live tail
 * change from a data swap (see `tailChange`).
 */
export function mergeLiveCandles(candles: readonly Candle[], incoming: readonly Candle[]): MergeResult {
  let out: Candle[] | null = null;
  for (const c of incoming) {
    const cur = out ?? candles;
    const last = cur[cur.length - 1];
    if (!last || c.time > last.time) {
      out = out ?? candles.slice();
      out.push(c);
      continue;
    }
    const i = candleLowerBound(cur, c.time);
    const old = cur[i];
    if (!old || old.time !== c.time || same(old, c)) continue;
    out = out ?? candles.slice();
    out[i] = c;
  }
  return out ? { candles: out, changed: true } : { candles: candles as Candle[], changed: false };
}

/**
 * Did `next` come from `prev` by a live update (same left edge, the last candle replaced and/or
 * one appended, the candles before it untouched)? Returns the first index that may differ
 * (= the previously newest candle: lightweight-charts rejects updates older than that), or null
 * when it is a different data set. Reference checks over the last few candles only: O(1).
 */
export function tailChange(prev: readonly Candle[], next: readonly Candle[]): number | null {
  if (prev.length === 0 || next.length < prev.length || next.length > prev.length + 1) return null;
  if (prev[0] !== next[0]) return null;
  const start = prev.length - 1;
  for (let i = Math.max(0, start - 2); i < start; i++) if (prev[i] !== next[i]) return null;
  return start;
}
