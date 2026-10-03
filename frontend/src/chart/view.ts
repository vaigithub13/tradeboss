/** Pure helpers for ChartView (no Lightweight Charts / React imports, so they are unit-tested). */
import type { Candle } from "../api/client";
import { candleLowerBound } from "../indicators/cache";

export interface LogicalRange {
  from: number;
  to: number;
}

/** What the chart was last given, to tell "older chunk prepended" apart from "new data set". */
export interface Snapshot {
  scope: string;
  firstTime: number;
  lastTime: number;
  length: number;
}

export const snapshotOf = (scope: string, candles: readonly Candle[]): Snapshot | null => {
  const first = candles[0];
  const last = candles[candles.length - 1];
  return first && last ? { scope, firstTime: first.time, lastTime: last.time, length: candles.length } : null;
};

/**
 * How many candles were prepended to the previously shown ones (0 = this is not a pure prepend:
 * other scope, other newest bar, or nothing new at the left). Same scope and same newest candle
 * and the old first candle now sitting at index n > 0 means n older candles were added.
 */
export function prependedCount(prev: Snapshot | null, scope: string, candles: readonly Candle[]): number {
  if (!prev || prev.scope !== scope) return 0;
  const last = candles[candles.length - 1];
  if (!last || last.time !== prev.lastTime) return 0;
  const i = candleLowerBound(candles, prev.firstTime);
  return candles[i]?.time === prev.firstTime ? i : 0;
}

/**
 * How the loaded window moved since the chart was last given data, in bars at the LEFT edge:
 *   n > 0  n older bars were added on the left   (a chunk was prepended; bars on the right may
 *          also have been dropped to keep the window bounded)
 *   n < 0  -n bars were dropped on the left      (a newer chunk was appended)
 *   0      same left edge (e.g. a bar was appended on the right)
 *   null   not a window move: other data set, or no overlap with what was shown
 * Anchored on whichever of the old first / last bar is still present, so it works for both
 * directions. Lets the chart keep the same bars on screen while the window slides.
 */
export function windowShift(prev: Snapshot | null, scope: string, candles: readonly Candle[]): number | null {
  if (!prev || prev.scope !== scope || candles.length === 0) return null;
  const firstAt = indexOfTime(candles, prev.firstTime);
  if (firstAt !== undefined) return firstAt;
  const lastAt = indexOfTime(candles, prev.lastTime);
  if (lastAt !== undefined) return lastAt - (prev.length - 1);
  return null;
}

/** The same bars stay on screen after `n` bars were added on the left. */
export const shiftRange = (r: LogicalRange, n: number): LogicalRange => ({ from: r.from + n, to: r.to + n });

/** Bars of history that must remain left of the visible area before the next chunk is fetched. */
export const PREFETCH_MIN_BARS = 300;

/** Fetch older candles when less than a screenful (at least PREFETCH_MIN_BARS) is left on the left. */
export const needsOlder = (r: LogicalRange): boolean => r.from < Math.max(PREFETCH_MIN_BARS, r.to - r.from);

/** Fetch newer candles when less than a screenful (at least PREFETCH_MIN_BARS) is left on the right. */
export const needsNewer = (r: LogicalRange, loaded: number): boolean =>
  r.to > loaded - Math.max(PREFETCH_MIN_BARS, r.to - r.from);

/** Last `bars` candles plus a little air on the right. */
export const initialRange = (n: number, bars: number, rightPad = 5): LogicalRange => ({
  from: Math.max(0, n - bars),
  to: n + rightPad,
});

/** Index of the candle that starts at `time`, or undefined. */
export function indexOfTime(candles: readonly Candle[], time: number): number | undefined {
  const i = candleLowerBound(candles, time);
  return candles[i]?.time === time ? i : undefined;
}

// ---- legend collapsed preference

export const LEGEND_COLLAPSED_KEY = "chart-analyser.legend.collapsed.v1";

export function loadLegendCollapsed(storage: Pick<Storage, "getItem"> | null): boolean {
  try {
    return storage?.getItem(LEGEND_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveLegendCollapsed(collapsed: boolean, storage: Pick<Storage, "setItem"> | null): void {
  try {
    storage?.setItem(LEGEND_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // private mode: just not remembered
  }
}
