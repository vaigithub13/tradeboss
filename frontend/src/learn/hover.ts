/** Hover read-out for the Learn chart: a price tag on the left edge and an O/H/L/C legend. */

import type { LearnBar } from "./structure";
import { learnAxisTime } from "./time";

export interface HoverPoint {
  time: number | null;
  price: number;
  y: number;
}

export function hoverPrice(price: number): string {
  return price.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** The hovered candle, or the latest one when the mouse is off the chart. */
export function legendBar(bars: readonly LearnBar[], time: number | null): LearnBar | null {
  if (time != null) {
    const hit = bars.find((bar) => bar.time === time);
    if (hit) return hit;
  }
  return bars.at(-1) ?? null;
}

export function legendText(bar: LearnBar | null): string {
  if (!bar) return "";
  const open = bar.open ?? bar.close;
  const change = bar.close - open;
  const sign = change > 0 ? "+" : change < 0 ? "−" : "";
  return `${learnAxisTime(bar.time)} IST  O ${hoverPrice(open)}  H ${hoverPrice(bar.high)}  L ${hoverPrice(bar.low)}  C ${hoverPrice(bar.close)}  ${sign}${hoverPrice(Math.abs(change))}`;
}
