import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";

import type { PineSignal } from "./signals";

function snapToCandle(time: number, candleTimes: readonly number[]): number | null {
  let snapped: number | null = null;
  for (const t of candleTimes) {
    if (t <= time) snapped = t;
    else break;
  }
  return snapped;
}

/** Chart markers for live Pine signals on one chart scope. */
export function pineMarkers(
  signals: readonly (PineSignal & { scriptId?: string; label?: string })[],
  candleTimes: readonly number[],
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  for (const s of signals) {
    const bar = snapToCandle(s.time, candleTimes);
    if (bar == null) continue;
    const buy = s.side === "BUY";
    const exit = s.side === "EXIT";
    out.push({
      time: bar as UTCTimestamp,
      position: buy ? "belowBar" : "aboveBar",
      shape: exit ? "circle" : buy ? "arrowUp" : "arrowDown",
      color: exit ? "#94a3b8" : buy ? "#26a69a" : "#ef5350",
      text: s.label ? `${s.label} ${s.text}` : s.text,
    } as SeriesMarker<Time>);
  }
  return out;
}
