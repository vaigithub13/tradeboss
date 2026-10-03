/**
 * Indicator cache helpers (pure).
 *
 * Values are cached per scope (symbol | timeframe | sessions) and per indicator KEY (type +
 * parameters, never colour / visibility / id). Each cached indicator is one contiguous series
 * of times + outputs. The chart loads candles in chunks (newest first, then older ones), so a
 * cached series only ever grows at its left (or, later with live data, right) end.
 */
import type { Candle } from "../api/client";
import type { IndicatorResult } from "../api/indicators";
import type { IndicatorType, Params } from "./catalog";

export interface IndicatorEntry {
  /** ascending candle start times covered by this entry */
  times: number[];
  outputs: Record<string, (number | null)[]>;
}

/** Same type + same parameters => same key (key order and instance id do not matter). */
export function indicatorKey(type: IndicatorType, params: Params): string {
  const sorted = Object.keys(params)
    .sort()
    .map((k) => [k, params[k]]);
  return `${type}:${JSON.stringify(sorted)}`;
}

export function scopeKey(symbol: string, timeframe: string, sessions: readonly string[]): string {
  return `${symbol}|${timeframe}|${sessions.join(",")}`;
}

/** First index i with key(i) >= t, for an ascending sequence of length n (binary search). */
function searchFirst(n: number, key: (i: number) => number, t: number): number {
  let lo = 0;
  let hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (key(mid) < t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** First index with times[i] >= t. */
export const lowerBound = (times: readonly number[], t: number): number =>
  searchFirst(times.length, (i) => times[i] ?? 0, t);

/** First candle index with time >= t. */
export const candleLowerBound = (candles: readonly Candle[], t: number): number =>
  searchFirst(candles.length, (i) => candles[i]?.time ?? 0, t);

export interface TimeRange {
  from: number;
  to: number;
}

/**
 * The candle ranges (inclusive start times) an indicator still has to be computed for:
 * everything if it has no entry yet, otherwise the candles left of / right of what it covers.
 */
export function missingRanges(candles: readonly Candle[], entry: IndicatorEntry | undefined): TimeRange[] {
  const first = candles[0];
  const last = candles[candles.length - 1];
  if (!first || !last) return [];
  const covered = entry && entry.times.length > 0 ? entry : undefined;
  if (!covered) return [{ from: first.time, to: last.time }];

  const coveredFrom = covered.times[0] ?? 0;
  const coveredTo = covered.times[covered.times.length - 1] ?? 0;
  const out: TimeRange[] = [];
  if (first.time < coveredFrom) {
    const endCandle = candles[candleLowerBound(candles, coveredFrom) - 1]; // last candle before the entry
    if (endCandle) out.push({ from: first.time, to: endCandle.time });
  }
  if (last.time > coveredTo) {
    const startCandle = candles[candleLowerBound(candles, coveredTo + 1)]; // first candle after the entry
    if (startCandle) out.push({ from: startCandle.time, to: last.time });
  }
  return out;
}

/** Merge a freshly computed chunk into an entry (overlaps are de-duplicated by time). */
export function mergeEntry(entry: IndicatorEntry | undefined, chunk: Pick<IndicatorResult, "outputs"> & { times: number[] }): IndicatorEntry {
  if (!entry || entry.times.length === 0) {
    return { times: chunk.times.slice(), outputs: cloneOutputs(chunk.outputs) };
  }
  const entryFrom = entry.times[0] ?? 0;
  const entryTo = entry.times[entry.times.length - 1] ?? 0;
  // part of the chunk strictly left of / right of the entry
  const leftEnd = lowerBound(chunk.times, entryFrom); // chunk[0:leftEnd] < entryFrom
  const rightStart = lowerBound(chunk.times, entryTo + 1); // chunk[rightStart:] > entryTo
  const times = [...chunk.times.slice(0, leftEnd), ...entry.times, ...chunk.times.slice(rightStart)];
  const outputs: Record<string, (number | null)[]> = {};
  for (const name of Object.keys(entry.outputs)) {
    const mine = entry.outputs[name] ?? [];
    const theirs = chunk.outputs[name] ?? [];
    outputs[name] = [...theirs.slice(0, leftEnd), ...mine, ...theirs.slice(rightStart)];
  }
  return { times, outputs };
}

const cloneOutputs = (o: Record<string, (number | null)[]>): Record<string, (number | null)[]> =>
  Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v.slice()]));

/** Value of one output at a candle start time (null when not covered / warming up). */
export function valueAt(entry: IndicatorEntry | undefined, output: string, time: number): number | null {
  if (!entry) return null;
  const i = lowerBound(entry.times, time);
  if (entry.times[i] !== time) return null;
  return entry.outputs[output]?.[i] ?? null;
}

/**
 * `entry` cut down to the bars in [from, to] (inclusive start times). Used to keep indicator
 * values bounded together with the chart window: values outside it are fetched again if the user
 * scrolls back. The result is still one contiguous series (or undefined when nothing is left).
 */
export function trimEntry(entry: IndicatorEntry, from: number, to: number): IndicatorEntry | undefined {
  const lo = lowerBound(entry.times, from);
  const hi = lowerBound(entry.times, to + 1);
  if (lo === 0 && hi === entry.times.length) return entry;
  if (lo >= hi) return undefined;
  const outputs: Record<string, (number | null)[]> = {};
  for (const [name, values] of Object.entries(entry.outputs)) outputs[name] = values.slice(lo, hi);
  return { times: entry.times.slice(lo, hi), outputs };
}

/**
 * Live update of the newest values: `chunk` (the last candles' values) overwrites the entry's
 * values at the same times and appends later ones. Returns undefined when the chunk does not
 * connect to the entry (the entry is missing, or bars lie between its end and the chunk): the
 * normal fetch then fills the hole, so a live tail can never leave a gap in the series.
 */
export function applyTail(
  entry: IndicatorEntry | undefined,
  chunk: { times: number[]; outputs: Record<string, (number | null)[]> },
): IndicatorEntry | undefined {
  if (!entry || entry.times.length === 0 || chunk.times.length === 0) return undefined;
  const first = chunk.times[0] ?? 0;
  const entryTo = entry.times[entry.times.length - 1] ?? 0;
  if (first > entryTo) return undefined; // a new bar after the entry: the normal fetch handles it
  if (first < (entry.times[0] ?? 0)) return undefined; // would drop the whole entry
  const keep = lowerBound(entry.times, first); // entry[0:keep] stays
  const times = [...entry.times.slice(0, keep), ...chunk.times];
  const outputs: Record<string, (number | null)[]> = {};
  for (const name of Object.keys(entry.outputs)) {
    outputs[name] = [...(entry.outputs[name] ?? []).slice(0, keep), ...(chunk.outputs[name] ?? chunk.times.map(() => null))];
  }
  return { times, outputs };
}
