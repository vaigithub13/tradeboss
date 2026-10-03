/** Replay requests never ask for, or keep, a bar after the cursor. */

import type { CandlesQuery } from "../api/client";
import type { IndicatorsRequest } from "../api/indicators";

export function capCandlesQuery(query: CandlesQuery, cursor: number): CandlesQuery & { cursor: number } {
  if (query.after != null && query.after >= cursor) {
    return { ...query, after: undefined, limit: undefined, to: cursor, cursor };
  }
  // `limit` and `after` cannot be combined with `to` on the server. `cursor` caps them.
  if (query.limit != null || query.after != null) return { ...query, cursor };
  const to = query.to == null ? cursor : Math.min(query.to, cursor);
  return { ...query, to, cursor };
}

export function capIndicatorsRequest(request: IndicatorsRequest, cursor: number): IndicatorsRequest {
  return {
    ...request,
    to: request.to == null ? cursor : Math.min(request.to, cursor),
    cursor,
  };
}

export function acceptBars<T extends { time: number }>(bars: readonly T[], cursor: number): T[] {
  const leak = bars.find((bar) => bar.time > cursor);
  if (leak) throw new Error(`response contains a bar after the cursor (${leak.time} > ${cursor})`);
  return [...bars];
}

/** Candles the chart may draw. The last close is the series last-price line. */
export function seriesThroughCursor<T extends { time: number; close: number }>(
  bars: readonly T[],
  cursor: number | null,
): { bars: readonly T[]; lastPrice: number | null } {
  const shown = cursor == null ? bars : bars.filter((bar) => bar.time <= cursor);
  const last = shown[shown.length - 1];
  return { bars: shown, lastPrice: last ? last.close : null };
}

export function clipSeries<T extends { times: number[]; outputs: Record<string, (number | null)[]> }>(
  entry: T | undefined,
  cursor: number | null,
): T | undefined {
  if (!entry || cursor == null) return entry;
  const end = entry.times.findIndex((time) => time > cursor);
  if (end < 0) return entry;
  const outputs: Record<string, (number | null)[]> = {};
  for (const [key, values] of Object.entries(entry.outputs)) outputs[key] = values.slice(0, end);
  return { ...entry, times: entry.times.slice(0, end), outputs };
}
