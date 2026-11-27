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

const CLUSTER = 0.25;

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

/** Cluster levels within 0.25 × the span. A zone is never wider than that step. */
export function clusterZones(levels: readonly ZoneLevel[], bars: readonly LearnBar[], atr: number, price: number): { zones: KeyZone[]; flips: ZoneFlip[] } {
  if (atr <= 0 || levels.length === 0) return { zones: [], flips: [] };
  const step = CLUSTER * atr;
  const sorted = [...levels].filter((level) => Number.isFinite(level.price)).sort((a, b) => a.price - b.price || a.time - b.time);
  const groups: ZoneLevel[][] = [];
  for (const level of sorted) {
    const group = groups[groups.length - 1];
    const anchor = group?.[0];
    const width = group ? level.price - Math.min(...group.map((item) => item.price)) : 0;
    if (!group || !anchor || level.price - anchor.price >= step || width >= step) groups.push([level]);
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
        detail: `${role === "support" ? "Support" : "Resistance"} ${money(low)}–${money(high)} flipped to ${next} on the close.`,
      });
      role = next;
      flipped = true;
    }
    const side = toneOf(low, high, price);
    const touch = touchesOf(bars, low, high, known);
    const sources = group.map((level) => level.name);
    const strength = sources.length + touch.count;
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
      label: labelFor(side, low, high, sources, strength),
      detail: `${place} ${money(low)}–${money(high)}. Sources: ${sources.join(", ")}. Touches: ${touch.count}. ${when}.${flipped ? " Flipped after a close through the zone." : ""}`,
      visible: false,
      faint: false,
    };
  });
  return { zones: markVisible(zones, price, atr), flips };
}

/**
 * Nearest zone on each side with strength at least 2, plus the strongest zone
 * within 1.5 × the span. Strength-1 zones inside that window are faint.
 * Two zones closer than 0.25 × the span keep the stronger one.
 */
export function markVisible(zones: readonly KeyZone[], price: number, atr: number): KeyZone[] {
  const distance = (zone: KeyZone) => (price > zone.high ? price - zone.high : zone.low > price ? zone.low - price : 0);
  const window = 1.5 * atr;
  const step = CLUSTER * atr;
  const chosen = new Map<string, "solid" | "faint">();
  for (const side of ["resistance", "support"] as const) {
    const list = zones.filter((zone) => zone.role === side);
    const nearest = [...list].filter((zone) => zone.strength >= 2).sort((a, b) => distance(a) - distance(b) || b.strength - a.strength)[0];
    const strongest = [...list].filter((zone) => distance(zone) <= window).sort((a, b) => b.strength - a.strength || distance(a) - distance(b))[0];
    for (const zone of [nearest, strongest]) {
      if (!zone) continue;
      const mode = zone.strength <= 1 ? "faint" : "solid";
      if (chosen.get(zone.id) !== "solid") chosen.set(zone.id, mode);
    }
  }
  for (const zone of zones) {
    if (zone.role === "inside") chosen.set(zone.id, zone.strength <= 1 ? "faint" : "solid");
    else if (zone.strength <= 1 && distance(zone) <= window && !chosen.has(zone.id)) chosen.set(zone.id, "faint");
  }
  const solids = zones.filter((zone) => chosen.get(zone.id) === "solid").sort((a, b) => a.low - b.low);
  for (let i = 1; i < solids.length; i += 1) {
    const prev = solids[i - 1]!;
    const next = solids[i]!;
    if (prev.role !== next.role || next.low - prev.high >= step) continue;
    chosen.delete((prev.strength >= next.strength ? next : prev).id);
  }
  return zones.map((zone) => ({
    ...zone,
    visible: chosen.get(zone.id) === "solid",
    faint: chosen.get(zone.id) === "faint",
  }));
}
