import { useMemo } from "react";

import type { Candle, Timeframe } from "../api/client";
import { SectionBoundary } from "../paper/SectionBoundary";
import {
  computeFirstHourBox,
  firstHourBoxVisibleOn,
  formatInrPrice,
  type FirstHourBoxResult,
} from "./firstHourBox";

/** Memoised first-hour boxes for the loaded (replay-clipped) candles. */
export function useFirstHourBox(
  candles: readonly Candle[],
  timeframe: Timeframe,
  cursor: number | null,
  enabled: boolean,
): FirstHourBoxResult {
  const lastClosed30 = useMemo(() => {
    if (!candles.length) return null;
    const last = candles[candles.length - 1]!.time;
    const cut = cursor != null ? Math.min(cursor, last) : last;
    return cut;
  }, [candles, cursor]);

  return useMemo(() => {
    if (!enabled || !firstHourBoxVisibleOn(timeframe) || !candles.length) {
      return { boxes: [], markers: [] };
    }
    return computeFirstHourBox(candles, { asOf: lastClosed30 });
  }, [candles, timeframe, enabled, lastClosed30]);
}

/** Compact A/B readout; errors stay inside the boundary. */
export function FirstHourBoxHud({
  result,
  enabled,
}: {
  result: FirstHourBoxResult;
  enabled: boolean;
}) {
  if (!enabled) return null;
  const today = result.boxes[result.boxes.length - 1];
  if (!today) return null;
  return (
    <SectionBoundary name="First Hour Box">
      <div
        className="pointer-events-none absolute left-2 top-10 z-10 rounded border border-white/10 bg-black/55 px-2 py-1 font-mono text-[11px] text-white/80 backdrop-blur-[2px]"
        data-testid="first-hour-box-hud"
      >
        {today.forming ? (
          <span className="text-amber-200">Box forming</span>
        ) : (
          <span>
            A {formatInrPrice(today.a)} · B {formatInrPrice(today.b)}
          </span>
        )}
      </div>
    </SectionBoundary>
  );
}
