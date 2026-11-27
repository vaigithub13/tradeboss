/** Chart-edge plans, study tooltips, and pill positions. Numbers come from closed candles. */

import type { KeyZone } from "./zones";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PlanEdge {
  id: string;
  side: "buy" | "sell";
  low: number;
  high: number;
  distance: number;
  above: boolean;
  text: string;
  detail: string;
  tip: string;
}

function money(price: number): string {
  return Math.round(price).toLocaleString("en-IN");
}

function span(low: number, high: number): string {
  return Math.round(low) === Math.round(high) ? money(low) : `${money(low)}–${money(high)}`;
}

function one(price: number): string {
  return price.toLocaleString("en-IN", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** A study reading. The reward is 2R from the zone edges. It is not an order. */
export function studyTip(tone: "buy" | "sell" | "inside", entry: number, sl: number, tp: number): string {
  const risk = Math.abs(entry - sl);
  const reward = Math.abs(tp - entry);
  const r = risk > 0 ? reward / risk : 0;
  const head = tone === "buy" ? "🟢 buy study" : tone === "sell" ? "🔴 sell study" : "⚪ inside zone";
  return `${head}\nEntry idea ${one(entry)} · SL ${one(sl)} · TP ${one(tp)} · ${r.toFixed(1)}R\nstudy only — not a trade`;
}

export function zoneStudy(zone: Pick<KeyZone, "low" | "high" | "role">, price: number): { tone: "buy" | "sell" | "inside"; entry: number; sl: number; tp: number } {
  if (zone.role === "inside" || (price >= zone.low && price <= zone.high)) {
    return { tone: "inside", entry: price, sl: zone.low, tp: zone.high };
  }
  if (zone.role === "resistance") {
    const risk = Math.max(zone.high - zone.low, 0.05);
    return { tone: "sell", entry: zone.low, sl: zone.high, tp: zone.low - 2 * risk };
  }
  const risk = Math.max(zone.high - zone.low, 0.05);
  return { tone: "buy", entry: zone.high, sl: zone.low, tp: zone.high + 2 * risk };
}

/** The nearest drawn zone above and below, pinned to the chart edge. */
export function planEdges(zones: readonly KeyZone[], price: number): PlanEdge[] {
  const distance = (zone: KeyZone) => (price > zone.high ? price - zone.high : zone.low > price ? zone.low - price : 0);
  const nearest = (role: "support" | "resistance") => [...zones]
    .filter((zone) => zone.visible && zone.role === role)
    .sort((a, b) => distance(a) - distance(b) || b.strength - a.strength)[0];
  const out: PlanEdge[] = [];
  const sell = nearest("resistance");
  const buy = nearest("support");
  if (sell) {
    const study = zoneStudy(sell, price);
    const gap = sell.low - price;
    out.push({
      id: `plan-${sell.id}`,
      side: "sell",
      low: sell.low,
      high: sell.high,
      distance: gap,
      above: true,
      text: `▲ Plan · SELL zone ${span(sell.low, sell.high)} · ${Math.abs(gap).toFixed(1)} above`,
      detail: sell.detail,
      tip: studyTip(study.tone, study.entry, study.sl, study.tp),
    });
  }
  if (buy) {
    const study = zoneStudy(buy, price);
    const gap = price - buy.high;
    out.push({
      id: `plan-${buy.id}`,
      side: "buy",
      low: buy.low,
      high: buy.high,
      distance: gap,
      above: false,
      text: `▼ Plan · BUY zone ${span(buy.low, buy.high)} · ${Math.abs(gap).toFixed(1)} below`,
      detail: buy.detail,
      tip: studyTip(study.tone, study.entry, study.sl, study.tp),
    });
  }
  return out;
}

export function signalPill(side: "buy" | "sell", status: string, clock: string, inZone: boolean): string {
  const word = status === "TP" ? "TP" : status === "SL" ? "SL" : status === "expired" ? "not counted" : inZone ? "in zone" : "waiting";
  return `● Plan ${side === "buy" ? "BUY" : "SELL"} · ${clock} ${word}`;
}

/** Sit beside the candle, clear of the body. */
export function placePatternPill(candleX: number, bodyTop: number, bodyBottom: number, above: boolean): Rect {
  const h = 14;
  const w = 58;
  const top = Math.min(bodyTop, bodyBottom);
  const bottom = Math.max(bodyTop, bodyBottom);
  return { x: candleX + 8, y: above ? top - h - 4 : bottom + 4, w, h };
}

export function rectsOverlap(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}
