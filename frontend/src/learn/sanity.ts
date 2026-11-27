/** A zone the chart may draw. Overnight gaps are NDOG/NWOG, not fair value gaps. */

import { atrAt, trueRanges } from "./lesson";
import type { LearnBar } from "./structure";
import { istClock } from "./time";

export const ZONE_MAX_ATR = 1.5;
export const ZONE_NEAR_ATR = 2;

export function atr14(bars: readonly LearnBar[], index: number): number {
  if (bars.length === 0 || index < 0) return 0;
  const trs = trueRanges(bars);
  const ready = atrAt(trs, Math.min(index, trs.length - 1), 14);
  if (ready != null) return ready;
  const used = trs.slice(0, Math.min(index, trs.length - 1) + 1);
  if (used.length === 0) return 0;
  return used.reduce((sum, value) => sum + value, 0) / used.length;
}

export function sameSession(a: number, b: number): boolean {
  return istClock(a).day === istClock(b).day;
}

/** True when the three candles share a session and the zone is at most 1.5 × ATR(14). */
export function gapFits(
  bars: readonly LearnBar[],
  leftIndex: number,
  rightIndex: number,
  top: number,
  bottom: number,
): boolean {
  const left = bars[leftIndex];
  const right = bars[rightIndex];
  if (!left || !right || !sameSession(left.time, right.time)) return false;
  const height = Math.abs(top - bottom);
  const atr = atr14(bars, rightIndex);
  return atr > 0 && height > 0 && height <= ZONE_MAX_ATR * atr;
}

export function nearPrice(low: number, high: number, last: number, atr: number): boolean {
  const top = Math.max(low, high);
  const bottom = Math.min(low, high);
  if (last >= bottom && last <= top) return true;
  const distance = last > top ? last - top : bottom - last;
  return atr > 0 && distance <= ZONE_NEAR_ATR * atr;
}
