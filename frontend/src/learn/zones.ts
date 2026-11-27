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
  /** Colour comes from where the last close sits, not from the zone's history. */
  role: "support" | "resistance" | "inside";
  flipped: boolean;
  sources: string[];
  touches: number;
  lastTest: number | null;
  strength: number;
  /**
   * Strength used by the draw filter. A part split off a wider cluster keeps the
   * parent's strength here, so a real level near price is not dropped. The dots
   * still show `strength`.
   */
  drawStrength?: number;
  label: string;
  detail: string;
  /** Drawn in the main view. */
  visible: boolean;
  /** Strength 1. Drawn only on the Full preset. */
  faint: boolean;
}

export interface ZoneFlip {
  time: number;
  text: string;
  detail: string;
}

/** Levels closer than this many ATR(14) of the chart timeframe share one zone. */
const CLUSTER = 0.25;
const CAP_ATR = 0.5;
const CAP_PRICE = 0.001;

/** Widest a zone may be: max(0.5 × ATR(14), 0.1% of price). */
export function zoneCap(atr: number, price: number): number {
  return Math.max(CAP_ATR * atr, CAP_PRICE * Math.abs(price));
}

export interface ZoneScale {
  /** ATR(14) of the chart timeframe. Sets the merge step and the width cap. */
  atr: number;
  /** Average daily range. Sets the window for the drawn zones. */
  span: number;
}

/** Chain levels within the step, then split any chain wider than the cap. */
function groupLevels(sorted: readonly ZoneLevel[], step: number, cap: number): { levels: ZoneLevel[]; parent: ZoneLevel[] }[] {
  const chains: ZoneLevel[][] = [];
  for (const level of sorted) {
    const chain = chains[chains.length - 1];
    const prev = chain?.[chain.length - 1];
    if (!chain || !prev || level.price - prev.price >= step) chains.push([level]);
    else chain.push(level);
  }
  const groups: { levels: ZoneLevel[]; parent: ZoneLevel[] }[] = [];
  for (const chain of chains) {
    let part: ZoneLevel[] = [];
    for (const level of chain) {
      if (part.length && level.price - part[0]!.price > cap) {
        groups.push({ levels: part, parent: chain });
        part = [];
      }
      part.push(level);
    }
    if (part.length) groups.push({ levels: part, parent: chain });
  }
  return groups;
}

function money(price: number): string {
  return Math.round(price).toLocaleString("en-IN");
}

const SOURCE: Record<string, string> = {
  "Swing high": "SH",
  "Swing low": "SL",
  "Week high": "PWH",
  "Week low": "PWL",
  "OR high": "OR",
  "OR low": "OR",
  "Accum high": "OR",
  "Accum low": "OR",
  "Round 100": "100",
  "Round 50": "50",
};

export function sourceTags(names: readonly string[]): string {
  const tags: string[] = [];
  for (const name of names) {
    const tag = SOURCE[name] ?? name;
    if (!tags.includes(tag)) tags.push(tag);
  }
  return tags.slice(0, 3).join("+");
}

export function strengthDots(strength: number): string {
  return "●".repeat(Math.min(5, Math.max(1, strength)));
}

function labelFor(role: "support" | "resistance" | "inside", low: number, high: number, sources: readonly string[], strength: number): string {
  const side = role === "resistance" ? "R" : role === "support" ? "S" : "In";
  const span = Math.round(low) === Math.round(high) ? money(low) : `${money(low)}–${money(high)}`;
  return `${side} ${span} · ${sourceTags(sources)} ${strengthDots(strength)}`;
}

/** Average of each session's true range. One session uses that day's high minus low. */
export function dailyRangeAtr(bars: readonly LearnBar[]): number {
  const days: { day: string; high: number; low: number; close: number }[] = [];
  for (const bar of bars) {
    const day = istClock(bar.time).day;
    const last = days[days.length - 1];
    if (!last || last.day !== day) {
      days.push({ day, high: bar.high, low: bar.low, close: bar.close });
      continue;
    }
    last.high = Math.max(last.high, bar.high);
    last.low = Math.min(last.low, bar.low);
    last.close = bar.close;
  }
  if (days.length === 0) return 0;
  let prev: number | null = null;
  const ranges = days.map((day) => {
    const tr = prev == null
      ? day.high - day.low
      : Math.max(day.high - day.low, Math.abs(day.high - prev), Math.abs(day.low - prev));
    prev = day.close;
    return tr;
  });
  const recent = ranges.slice(-14);
  return recent.reduce((sum, range) => sum + range, 0) / recent.length;
}

function toneOf(low: number, high: number, price: number): "support" | "resistance" | "inside" {
  if (price < low) return "resistance";
  if (price > high) return "support";
  return "inside";
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

/**
 * Cluster levels within 0.25 × ATR(14) of the chart timeframe. A cluster wider
 * than max(0.5 × ATR, 0.1% of price) is split; each part keeps its own sources.
 * A plain number sets both the ATR and the span (used by older callers and tests).
 */
export function clusterZones(
  levels: readonly ZoneLevel[],
  bars: readonly LearnBar[],
  scale: number | ZoneScale,
  price: number,
): { zones: KeyZone[]; flips: ZoneFlip[] } {
  const { atr, span } = typeof scale === "number" ? { atr: scale, span: scale } : scale;
  if (atr <= 0 || levels.length === 0) return { zones: [], flips: [] };
  const step = CLUSTER * atr;
  const sorted = [...levels].filter((level) => Number.isFinite(level.price)).sort((a, b) => a.price - b.price || a.time - b.time);
  const groups = groupLevels(sorted, step, zoneCap(atr, price));
  const flips: ZoneFlip[] = [];
  const zones: KeyZone[] = groups.map(({ levels: group, parent }, index) => {
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
        detail: `${role === "support" ? "Support" : "Resistance"} ${money(low)}–${money(high)} flipped to ${next} on the close.`,
      });
      role = next;
      flipped = true;
    }
    const side = toneOf(low, high, price);
    const touch = touchesOf(bars, low, high, known);
    const sources = group.map((level) => level.name);
    const strength = sources.length + touch.count;
    let drawStrength = strength;
    if (parent.length > group.length) {
      const parentLow = parent[0]!.price;
      const parentHigh = parent[parent.length - 1]!.price;
      const parentKnown = Math.max(...parent.map((level) => level.time));
      drawStrength = Math.max(strength, parent.length + touchesOf(bars, parentLow, parentHigh, parentKnown).count);
    }
    const when = touch.last == null ? "no test yet" : `last test ${istClock(touch.last).day}`;
    const place = side === "inside" ? "price inside zone" : side === "resistance" ? "Resistance" : "Support";
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
      drawStrength,
      label: labelFor(side, low, high, sources, strength),
      detail: `${place} ${money(low)}–${money(high)}. Sources: ${sources.join(", ")}. Touches: ${touch.count}. ${when}.${flipped ? " Flipped after a close through the zone." : ""}`,
      visible: false,
      faint: false,
    };
  });
  return { zones: markVisible(zones, price, span > 0 ? span : atr, step), flips };
}

/**
 * Nearest zone on each side with strength at least 2, plus the strongest zone
 * within 1.5 × the span. Strength-1 zones inside that window are faint.
 * The nearest zone on each side is always drawn, even with one dot.
 * Two drawn zones closer than `step` keep the stronger one, but never drop a nearest zone.
 */
export function markVisible(zones: readonly KeyZone[], price: number, atr: number, gap = CLUSTER * atr): KeyZone[] {
  const distance = (zone: KeyZone) => (price > zone.high ? price - zone.high : zone.low > price ? zone.low - price : 0);
  const window = 1.5 * atr;
  const step = gap;
  const power = (zone: KeyZone) => zone.drawStrength ?? zone.strength;
  const chosen = new Map<string, "solid" | "faint">();
  const closest = new Set<string>();
  for (const side of ["resistance", "support"] as const) {
    const list = zones.filter((zone) => zone.role === side);
    const near = [...list].sort((a, b) => distance(a) - distance(b) || power(b) - power(a))[0];
    if (near) {
      closest.add(near.id);
      chosen.set(near.id, "solid");
    }
    const nearest = [...list].filter((zone) => power(zone) >= 2).sort((a, b) => distance(a) - distance(b) || power(b) - power(a))[0];
    const strongest = [...list].filter((zone) => distance(zone) <= window).sort((a, b) => power(b) - power(a) || distance(a) - distance(b))[0];
    for (const zone of [nearest, strongest]) {
      if (!zone) continue;
      const mode = power(zone) <= 1 ? "faint" : "solid";
      if (chosen.get(zone.id) !== "solid") chosen.set(zone.id, mode);
    }
  }
  for (const zone of zones) {
    if (zone.role === "inside") chosen.set(zone.id, power(zone) <= 1 ? "faint" : "solid");
    else if (power(zone) <= 1 && distance(zone) <= window && !chosen.has(zone.id)) chosen.set(zone.id, "faint");
  }
  const solids = zones.filter((zone) => chosen.get(zone.id) === "solid").sort((a, b) => a.low - b.low);
  for (let i = 1; i < solids.length; i += 1) {
    const prev = solids[i - 1]!;
    const next = solids[i]!;
    if (prev.role !== next.role || next.low - prev.high >= step) continue;
    if (closest.has(prev.id) || closest.has(next.id)) {
      const other = closest.has(prev.id) ? next : prev;
      if (!closest.has(other.id)) chosen.delete(other.id);
      continue;
    }
    chosen.delete((power(prev) >= power(next) ? next : prev).id);
  }
  return zones.map((zone) => ({
    ...zone,
    visible: chosen.get(zone.id) === "solid",
    faint: chosen.get(zone.id) === "faint",
  }));
}
