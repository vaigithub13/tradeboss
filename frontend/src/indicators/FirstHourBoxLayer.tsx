import { useMemo } from "react";

import type { Candle, Timeframe } from "../api/client";
import { SectionBoundary } from "../paper/SectionBoundary";
import {
  computeFirstHourBox,
  firstHourBoxVisibleOn,
  formatBoxAxisLabel,
  formatInrPrice,
  istHhmm,
  type FirstHourBoxResult,
} from "./firstHourBox";
import type { IndicatorInstance } from "./catalog";

export function useFirstHourBox(
  candles: readonly Candle[],
  timeframe: Timeframe,
  cursor: number | null,
  enabled: boolean,
  maxWidth: number,
): FirstHourBoxResult {
  const asOf = useMemo(() => {
    if (!candles.length) return null;
    const last = candles[candles.length - 1]!.time;
    return cursor != null ? Math.min(cursor, last) : last;
  }, [candles, cursor]);

  return useMemo(() => {
    if (!enabled || !firstHourBoxVisibleOn(timeframe) || !candles.length) {
      return { boxes: [], markers: [], nextCloseTime: null };
    }
    return computeFirstHourBox(candles, { asOf, maxWidth });
  }, [candles, timeframe, enabled, asOf, maxWidth]);
}

export interface FhbUiColors {
  box: string;
  high: string;
  low: string;
  mid: string;
  probe: string;
  break: string;
  accepted_up: string;
  accepted_down: string;
  failed: string;
}

export function fhbSettings(item: IndicatorInstance | undefined): {
  maxWidth: number;
  markers: boolean;
  statusPanel: boolean;
  colors: FhbUiColors;
} {
  const p = item?.params ?? {};
  const c = item?.colors ?? {};
  const pick = (key: keyof FhbUiColors, fallback: string): string => {
    const v = c[key];
    return typeof v === "string" && v.length > 0 ? v : fallback;
  };
  return {
    maxWidth: Number(p["max_width"] ?? 150),
    markers: p["markers"] !== "off",
    statusPanel: p["status_panel"] !== "off",
    colors: {
      box: pick("box", "#2962ff"),
      high: pick("high", "#16a34a"),
      low: pick("low", "#dc2626"),
      mid: pick("mid", "#94a3b8"),
      probe: pick("probe", "#f59e0b"),
      break: pick("break", "#3b82f6"),
      accepted_up: pick("accepted_up", "#16a34a"),
      accepted_down: pick("accepted_down", "#dc2626"),
      failed: pick("failed", "#a855f7"),
    },
  };
}

/** Chart-corner status: H/L/Mid/Width · last status · next close. */
export function FirstHourBoxHud({
  result,
  enabled,
  showPanel,
}: {
  result: FirstHourBoxResult;
  enabled: boolean;
  showPanel: boolean;
}) {
  if (!enabled || !showPanel) return null;
  const box = result.boxes[result.boxes.length - 1];
  if (!box) return null;
  return (
    <SectionBoundary name="First Hour Box">
      <div
        className="pointer-events-none absolute left-2 top-10 z-10 max-w-[22rem] rounded border border-white/10 bg-black/60 px-2 py-1.5 font-mono text-[11px] text-white/80 backdrop-blur-[2px]"
        data-testid="first-hour-box-hud"
      >
        {box.forming ? (
          <div className="text-amber-200">Box forming</div>
        ) : (
          <div>
            H {formatInrPrice(box.high)} · L {formatInrPrice(box.low)} · Mid {formatInrPrice(box.mid)} · W{" "}
            {formatInrPrice(box.width)}
          </div>
        )}
        {box.tooWide && (
          <div className="text-amber-300" data-testid="fhb-too-wide">
            Box too wide – Strategy C off
          </div>
        )}
        <div className="mt-0.5 text-white/55">
          {box.lastStatusTime != null ? (
            <>
              {istHhmm(box.lastStatusTime)} · {box.lastStatusLabel}
            </>
          ) : (
            box.lastStatusLabel
          )}
          {result.nextCloseTime != null && <> · next {istHhmm(result.nextCloseTime)}</>}
        </div>
      </div>
    </SectionBoundary>
  );
}

export { formatBoxAxisLabel };
