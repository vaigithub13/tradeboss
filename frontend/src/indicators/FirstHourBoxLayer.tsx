import { useMemo } from "react";

import type { Candle, Timeframe } from "../api/client";
import { SectionBoundary } from "../paper/SectionBoundary";
import {
  computeFirstHourBox,
  firstHourBoxVisibleOn,
  formatBoxAxisLabel,
  formatInrPrice,
  formatNextCloseHint,
  istHhmm,
  type FirstHourBoxResult,
  type FhbMarker,
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
  showPastDays: boolean;
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
    showPastDays: p["show_past_days"] === "on",
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

/** Compact bottom-left status — never overlaps OHLC header / Bullish badge. */
export function FirstHourBoxHud({
  result,
  enabled,
  showPanel,
  asOf,
}: {
  result: FirstHourBoxResult;
  enabled: boolean;
  showPanel: boolean;
  asOf?: number | null;
}) {
  if (!enabled || !showPanel) return null;
  const box = result.boxes[result.boxes.length - 1];
  if (!box) return null;
  const nextHint = formatNextCloseHint(asOf ?? box.lastStatusTime, result.nextCloseTime);
  return (
    <SectionBoundary name="First Hour Box">
      <div
        className="pointer-events-none absolute bottom-10 left-2 z-10 max-w-[18rem] rounded border border-white/10 bg-black/55 px-2 py-1 font-mono text-[10px] leading-snug text-white/80 backdrop-blur-[2px]"
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
          <div className="text-orange-300" data-testid="fhb-too-wide">
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
          {nextHint != null && <> · {nextHint}</>}
        </div>
      </div>
    </SectionBoundary>
  );
}

/** Hover tooltip for a one-letter marker (full status text). */
export function FirstHourBoxMarkerTip({
  marker,
  enabled,
}: {
  marker: FhbMarker | null;
  enabled: boolean;
}) {
  if (!enabled || !marker) return null;
  return (
    <div
      className="pointer-events-none absolute bottom-20 left-2 z-10 rounded border border-white/15 bg-black/70 px-2 py-0.5 font-mono text-[10px] text-white/90"
      data-testid="fhb-marker-tip"
      style={{ borderColor: `${marker.color}66`, color: marker.color }}
    >
      {marker.label}
    </div>
  );
}

export { formatBoxAxisLabel };
