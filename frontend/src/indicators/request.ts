import type { Candle, SessionType, Timeframe } from "../api/client";
import type { IndicatorsRequest } from "../api/indicators";
import { hasVolume } from "../chart/volume";
import { unavailableReason, validateParams, type IndicatorInstance, type IndicatorType, type Params } from "./catalog";
import { indicatorKey, missingRanges, type IndicatorEntry, type TimeRange } from "./cache";

export interface RequestContext {
  symbol: string;
  timeframe: Timeframe;
  sessions: readonly SessionType[];
  candles: readonly Candle[];
  items: readonly IndicatorInstance[];
}

/** Items that should be computed now: visible, valid parameters, and usable on this data. */
export function activeItems(ctx: RequestContext): IndicatorInstance[] {
  const volume = hasVolume(ctx.candles);
  return ctx.items.filter(
    (i) =>
      i.visible &&
      validateParams(i.type, i.params) === null &&
      unavailableReason(i.type, { timeframe: ctx.timeframe, hasVolume: volume }) === null,
  );
}

/** One backend call: a candle range plus the (distinct) indicators that still lack values there. */
export interface FetchGroup {
  range: TimeRange;
  indicators: { key: string; type: IndicatorType; params: Params }[];
}

/**
 * What still has to be fetched, given what is already cached for this scope. Two instances with
 * the same type + parameters share one computation; colour / visibility never matter; an
 * indicator that is fully covered is not requested at all.
 */
export function planFetches(ctx: RequestContext, cached: Readonly<Record<string, IndicatorEntry>>): FetchGroup[] {
  const groups = new Map<string, FetchGroup>();
  const seen = new Set<string>();
  for (const item of activeItems(ctx)) {
    const key = indicatorKey(item.type, item.params);
    if (seen.has(key)) continue;
    seen.add(key);
    for (const range of missingRanges(ctx.candles, cached[key])) {
      const gk = `${range.from}-${range.to}`;
      let group = groups.get(gk);
      if (!group) groups.set(gk, (group = { range, indicators: [] }));
      group.indicators.push({ key, type: item.type, params: { ...item.params } });
    }
  }
  return [...groups.values()];
}

/** Request body for one group (same symbol / timeframe / session filter as the chart). */
export function buildIndicatorsRequest(ctx: RequestContext, group: FetchGroup): IndicatorsRequest {
  return {
    symbol: ctx.symbol,
    timeframe: ctx.timeframe,
    from: group.range.from,
    to: group.range.to,
    sessions: [...ctx.sessions],
    // wire id = position in `group.indicators`, so results map straight back to their cache key
    indicators: group.indicators.map((i, n) => ({ id: String(n), type: i.type, params: i.params })),
  };
}
