/** Nearby levels become one support or resistance band. */

import type { LearnBar } from "./structure";
import { istClock } from "./time";

export interface ZoneLevel {
  name: string;
  price: number;
  time: number;
}

export interface KeyZone {
  id: string;
  low: number;
  high: number;
  role: "support" | "resistance";
  flipped: boolean;
  sources: string[];
  touches: number;
  lastTest: number | null;
  strength: number;
  label: string;
  detail: string;
  visible: boolean;
}

export interface ZoneFlip {
  time: number;
  text: string;
  detail: string;
}

const CLUSTER = 0.25;

function money(price: number): string {
  return Math.round(price).toLocaleString("en-IN");
}

function labelFor(role: "support" | "resistance", low: number, high: number, strength: number): string {
  const side = role === "resistance" ? "R" : "S";
  const span = Math.round(low) === Math.round(high) ? money(low) : `${money(low)}–${money(high)}`;
  return `${side} ${span} · ${strength}`;
}

function touchesOf(bars: readonly LearnBar[], low: number, high: number, known: number): { count: number; last: number | null } {
  let count = 0;
  let last: number | null = null;
  for (const bar of bars) {
    if (bar.time <= known) continue;
    if (bar.high < low || bar.low > high) continue;
    count += 1;
    last = bar.time;
  }
  return { count, last };
}

/** Cluster levels within 0.25 × ATR. A zone is never wider than that step. */
export function clusterZones(levels: readonly ZoneLevel[], bars: readonly LearnBar[], atr: number, price: number): { zones: KeyZone[]; flips: ZoneFlip[] } {
  if (atr <= 0 || levels.length === 0) return { zones: [], flips: [] };
  const step = CLUSTER * atr;
  const sorted = [...levels].filter((level) => Number.isFinite(level.price)).sort((a, b) => a.price - b.price || a.time - b.time);
  const groups: ZoneLevel[][] = [];
  for (const level of sorted) {
    const group = groups[groups.length - 1];
    const anchor = group?.[0];
    if (!group || !anchor || level.price - anchor.price > step) groups.push([level]);
    else group.push(level);
  }
  const flips: ZoneFlip[] = [];
  const zones: KeyZone[] = groups.map((group, index) => {
    const low = Math.min(...group.map((level) => level.price));
    const high = Math.max(...group.map((level) => level.price));
    const known = Math.max(...group.map((level) => level.time));
    const knownBar = [...bars].reverse().find((bar) => bar.time <= known) ?? bars[0];
    let role: "support" | "resistance" = (knownBar?.close ?? price) >= (low + high) / 2 ? "support" : "resistance";
    let flipped = false;
    for (const bar of bars) {
      if (bar.time <= known) continue;
      const broke = role === "support" ? bar.close < low : bar.close > high;
      if (!broke) continue;
      const next = role === "support" ? "resistance" : "support";
      flips.push({
        time: bar.time,
        text: `${role === "support" ? "Support" : "Resistance"} flipped to ${next}`,
        detail: `${labelFor(role, low, high, group.length)} flipped to ${next} on the close.`,
      });
      role = next;
      flipped = true;
    }
    const side: "support" | "resistance" = price >= low && price <= high
      ? role
      : price > high ? "support" : "resistance";
    const touch = touchesOf(bars, low, high, known);
    const sources = group.map((level) => level.name);
    const strength = sources.length + touch.count;
    const when = touch.last == null ? "no test yet" : `last test ${istClock(touch.last).day}`;
    return {
      id: `zone-${index}-${Math.round(low)}-${Math.round(high)}`,
      low,
      high,
      role: side,
      flipped,
      sources,
      touches: touch.count,
      lastTest: touch.last,
      strength,
      label: labelFor(side, low, high, strength),
      detail: `${side === "resistance" ? "Resistance" : "Support"} ${money(low)}–${money(high)}. Sources: ${sources.join(", ")}. Touches: ${touch.count}. ${when}.${flipped ? " Flipped after a close through the zone." : ""}`,
      visible: false,
    };
  });
  return { zones: markVisible(zones, price), flips };
}

/** The two nearest zones above price and the two nearest below. */
export function markVisible(zones: readonly KeyZone[], price: number): KeyZone[] {
  const distance = (zone: KeyZone) => (price > zone.high ? price - zone.high : zone.low > price ? zone.low - price : 0);
  const pick = (side: "support" | "resistance") => zones
    .filter((zone) => zone.role === side)
    .sort((a, b) => distance(a) - distance(b) || b.strength - a.strength)
    .slice(0, 2)
    .map((zone) => zone.id);
  const shown = new Set([...pick("resistance"), ...pick("support")]);
  return zones.map((zone) => ({ ...zone, visible: shown.has(zone.id) }));
}
