/**
 * Learn-tab lesson. Closed bars only. Nothing here is a signal or an order.
 *
 * Equal highs (EQH) and equal lows (EQL): two or more confirmed swing highs (lows)
 * where each later swing is within 0.1 × ATR(14) of the cluster's first price.
 * ATR(14) is the simple mean of the last 14 true ranges, ending at that later
 * swing's confirmation bar. The drawn price is the average of the cluster.
 * A swing that is not confirmed yet is not in the list, so a future bar cannot
 * create the level early.
 *
 * Other liquidity, tagged BSL when the price is at or above the last close and
 * SSL when it is below: PDH, PDL, previous-week high and low, and the opening-range
 * edges. Those are known at the first bar of the latest session, except the
 * opening range, which is known at the first bar at or after 09:45 IST.
 *
 * Sweep: a candle trades strictly beyond the level and closes back on the
 * original side. The close-back may be that same candle or the next one only.
 * The mark is placed on the close-back candle. If that next candle is not in
 * the closed set, there is no sweep yet.
 *
 * AMD, intraday Nifty / Sensex / Bank Nifty only, one pass per IST session.
 * Opening hour: bars whose start is in [09:15, 10:15). Its range is high − low.
 * The threshold is the mean of up to 20 earlier opening hours on this chart.
 * Accumulation runs from 09:15 until the running session range exceeds 1.0 × that
 * threshold, or until the first bar at or after 10:15, whichever comes first.
 * The end bar is included. Its high and low are the balance.
 * Manipulation is the first sweep, on a bar after that window, of the accumulation
 * high, the accumulation low, PDH, or PDL, whose close-back candle also closes
 * inside the accumulation range. The chart shades it orange.
 * Distribution is a later close beyond the opposite side of that range: above
 * the accumulation high after a low-side sweep, or below the accumulation low
 * after a high-side sweep. The shade runs until a CHoCH against that direction
 * or the first bar at or after 15:15, whichever is first.
 * If the session has reached 15:15, or a later session is already on the chart,
 * and manipulation or distribution never happened, the day is "No AMD today".
 * Before 15:15 that label is withheld.
 *
 * Textbook entry (learning only): after a manipulation and a later CHoCH in the
 * opposite direction. The zone is the first 3-candle fair value gap in that leg
 * (bullish: candle 3 low above candle 1 high; bearish: candle 3 high below candle
 * 1 low). If that gap is not there, the zone is the 50% of the leg from the sweep
 * extreme to the CHoCH close. The stop is the sweep extreme. The target is the
 * nearest opposite liquidity beyond the zone, including the other side of the
 * accumulation range. What happened after — target hit, stop hit, or neither by
 * 15:15 — is filled only from candles after the CHoCH. A bar that reaches both
 * counts as a stop. A missing 15:15 bar leaves the outcome empty.
 */

import { istParts, type LevelLine } from "./levels";
import type { LearnBar, Structure, Swing } from "./structure";

const OPEN = 9 * 60 + 15;
const HOUR = 10 * 60 + 15;
const FLAT = 15 * 60 + 15;
const CLOSE = 15 * 60 + 30;
const ATR = 14;

const INTRADAY = new Set(["1m", "3m", "5m", "15m", "30m", "1h"]);
const AMD_SYMBOLS = new Set(["NIFTY50", "SENSEX", "BANKNIFTY"]);

const REFERENCE: Record<string, { name: string; side: "high" | "low" }> = {
  pdh: { name: "PDH", side: "high" },
  pdl: { name: "PDL", side: "low" },
  "week-high": { name: "Week high", side: "high" },
  "week-low": { name: "Week low", side: "low" },
  "or-high": { name: "OR high", side: "high" },
  "or-low": { name: "OR low", side: "low" },
};

export interface Liquidity {
  id: string;
  tag: "EQH" | "EQL" | "BSL" | "SSL";
  name: string;
  price: number;
  side: "high" | "low";
  knownAt: number;
}

export interface Sweep {
  index: number;
  startIndex: number;
  time: number;
  from: number;
  name: string;
  text: string;
  price: number;
  side: "high" | "low";
  extreme: number;
}

export interface Textbook {
  from: number;
  to: number;
  priceLow: number;
  priceHigh: number;
  stop: number;
  target: number | null;
  outcome: "target hit" | "stop hit" | "neither by 15:15" | null;
}

export interface AmdSession {
  day: string;
  accumulation: { from: number; to: number; high: number; low: number; endIndex: number } | null;
  manipulation: Sweep | null;
  distribution: { from: number; to: number; direction: "up" | "down" } | null;
  note: "No AMD today" | null;
  textbook: Textbook | null;
}

export interface Lesson {
  liquidity: Liquidity[];
  sweeps: Sweep[];
  sessions: AmdSession[];
}

export interface LessonInput {
  bars: readonly LearnBar[];
  structure: Structure;
  levels: readonly LevelLine[];
  timeframe?: string;
  symbol?: string;
}

export function trueRanges(bars: readonly LearnBar[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < bars.length; i += 1) {
    const bar = bars[i]!;
    const prev = i > 0 ? bars[i - 1]!.close : bar.close;
    out.push(Math.max(bar.high - bar.low, Math.abs(bar.high - prev), Math.abs(bar.low - prev)));
  }
  return out;
}

export function atrAt(trs: readonly number[], index: number, length = ATR): number | null {
  if (index < length - 1 || index >= trs.length) return null;
  let sum = 0;
  for (let i = index - length + 1; i <= index; i += 1) sum += trs[i]!;
  return sum / length;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export function equalLiquidity(swings: readonly Swing[], bars: readonly LearnBar[]): Liquidity[] {
  const trs = trueRanges(bars);
  const out: Liquidity[] = [];
  for (const kind of ["high", "low"] as const) {
    const clusters: { anchor: number; prices: number[]; knownAt: number }[] = [];
    for (const swing of swings) {
      if (swing.kind !== kind) continue;
      const atr = atrAt(trs, swing.confirmIndex);
      if (atr == null) continue;
      const tol = 0.1 * atr + 1e-6;
      const cluster = clusters.find((item) => Math.abs(swing.price - item.anchor) <= tol);
      if (cluster) {
        cluster.prices.push(swing.price);
        cluster.knownAt = swing.confirmIndex;
      } else {
        clusters.push({ anchor: swing.price, prices: [swing.price], knownAt: swing.confirmIndex });
      }
    }
    clusters.filter((item) => item.prices.length >= 2).forEach((item, index) => {
      const price = round2(item.prices.reduce((sum, value) => sum + value, 0) / item.prices.length);
      const tag = kind === "high" ? "EQH" : "EQL";
      out.push({
        id: `${tag}-${index}-${price}`,
        tag,
        name: tag,
        price,
        side: kind,
        knownAt: item.knownAt,
      });
    });
  }
  return out;
}

function tagFor(price: number, lastClose: number): "BSL" | "SSL" {
  return price < lastClose ? "SSL" : "BSL";
}

export function referenceLiquidity(bars: readonly LearnBar[], levels: readonly LevelLine[]): Liquidity[] {
  if (bars.length === 0) return [];
  const last = bars[bars.length - 1]!;
  const today = istParts(last.time).day;
  const dayStart = bars.findIndex((bar) => istParts(bar.time).day === today);
  const orAt = bars.findIndex((bar) => {
    const parts = istParts(bar.time);
    return parts.day === today && parts.minutes >= 9 * 60 + 45;
  });
  const out: Liquidity[] = [];
  for (const level of levels) {
    const ref = REFERENCE[level.id];
    if (!ref) continue;
    const knownAt = level.id.startsWith("or-") ? orAt : dayStart;
    if (knownAt < 0) continue;
    out.push({
      id: `liq-${level.id}`,
      tag: tagFor(level.price, last.close),
      name: ref.name,
      price: level.price,
      side: ref.side,
      knownAt,
    });
  }
  return out;
}

function beyond(bar: LearnBar, level: Pick<Liquidity, "price" | "side">): boolean {
  return level.side === "high" ? bar.high > level.price : bar.low < level.price;
}

function backAcross(bar: LearnBar, level: Pick<Liquidity, "price" | "side">): boolean {
  return level.side === "high" ? bar.close < level.price : bar.close > level.price;
}

function extremeOf(bars: readonly LearnBar[], from: number, to: number, side: "high" | "low"): number {
  let value = side === "high" ? -Infinity : Infinity;
  for (let i = from; i <= to; i += 1) {
    const bar = bars[i]!;
    value = side === "high" ? Math.max(value, bar.high) : Math.min(value, bar.low);
  }
  return value;
}

/** Sweeps of one level. The confirming candle is in `bars`, or the sweep is omitted. */
export function liquiditySweeps(bars: readonly LearnBar[], level: Liquidity): Sweep[] {
  const out: Sweep[] = [];
  let i = Math.max(0, level.knownAt);
  while (i < bars.length) {
    const bar = bars[i]!;
    if (!beyond(bar, level)) {
      i += 1;
      continue;
    }
    if (backAcross(bar, level)) {
      out.push(toSweep(bars, level, i, i));
      i += 1;
      continue;
    }
    const next = bars[i + 1];
    if (!next) break;
    if (backAcross(next, level)) out.push(toSweep(bars, level, i, i + 1));
    i += 2;
  }
  return out;
}

function toSweep(bars: readonly LearnBar[], level: Pick<Liquidity, "name" | "price" | "side">, start: number, end: number): Sweep {
  return {
    index: end,
    startIndex: start,
    time: bars[end]!.time,
    from: bars[start]!.time,
    name: level.name,
    text: `Sweep of ${level.name}`,
    price: level.price,
    side: level.side,
    extreme: extremeOf(bars, start, end, level.side),
  };
}

function inside(bar: LearnBar, low: number, high: number): boolean {
  return bar.close >= low && bar.close <= high;
}

function firstManipulation(
  bars: readonly LearnBar[],
  levels: readonly Liquidity[],
  start: number,
  low: number,
  high: number,
): Sweep | null {
  let best: Sweep | null = null;
  for (const level of levels) {
    let i = Math.max(start, level.knownAt);
    while (i < bars.length) {
      const bar = bars[i]!;
      if (!beyond(bar, level)) {
        i += 1;
        continue;
      }
      if (backAcross(bar, level) && inside(bar, low, high)) {
        const sweep = toSweep(bars, level, i, i);
        best = prefer(best, sweep, level.price);
        break;
      }
      const next = bars[i + 1];
      if (next && backAcross(next, level) && inside(next, low, high)) {
        const sweep = toSweep(bars, level, i, i + 1);
        best = prefer(best, sweep, level.price);
        break;
      }
      if (!next) break;
      i += 2;
    }
  }
  return best;
}

function prefer(current: Sweep | null, next: Sweep, level: number): Sweep {
  if (!current || next.index < current.index) return next;
  if (next.index > current.index) return current;
  const nextPen = Math.abs(next.extreme - level);
  const currentPen = Math.abs(current.extreme - current.price);
  return nextPen > currentPen ? next : current;
}

export function entryZone(
  bars: readonly LearnBar[],
  startIndex: number,
  chochIndex: number,
  direction: "up" | "down",
  extreme: number,
): { low: number; high: number } {
  for (let i = startIndex + 2; i <= chochIndex; i += 1) {
    const first = bars[i - 2]!;
    const third = bars[i]!;
    if (direction === "up" && third.low > first.high) return { low: first.high, high: third.low };
    if (direction === "down" && third.high < first.low) return { low: third.high, high: first.low };
  }
  const mid = (extreme + bars[chochIndex]!.close) / 2;
  return { low: mid, high: mid };
}

function outcomeOf(
  bars: readonly LearnBar[],
  chochIndex: number,
  direction: "up" | "down",
  stop: number,
  target: number | null,
): Textbook["outcome"] {
  for (let i = chochIndex + 1; i < bars.length; i += 1) {
    const bar = bars[i]!;
    const stopHit = direction === "up" ? bar.low <= stop : bar.high >= stop;
    const targetHit = target != null && (direction === "up" ? bar.high >= target : bar.low <= target);
    if (stopHit) return "stop hit";
    if (targetHit) return "target hit";
    if (istParts(bar.time).minutes >= FLAT) return "neither by 15:15";
  }
  return null;
}

function textbookFor(
  bars: readonly LearnBar[],
  sessionBars: { index: number; minutes: number }[],
  manipulation: Sweep,
  structure: Structure,
  liquidity: readonly Liquidity[],
  accumHigh: number,
  accumLow: number,
): Textbook | null {
  const direction = manipulation.side === "low" ? "up" : "down";
  const choch = structure.events.find((event) => event.kind === "choch" && event.index > manipulation.index && event.direction === direction);
  if (!choch || choch.index >= bars.length) return null;
  const zone = entryZone(bars, manipulation.startIndex, choch.index, direction, manipulation.extreme);
  const candidates: { price: number }[] = [
    ...liquidity.filter((level) => level.side === (direction === "up" ? "high" : "low")),
    { price: direction === "up" ? accumHigh : accumLow },
  ];
  const beyondZone = candidates.filter((level) => direction === "up" ? level.price > zone.high : level.price < zone.low);
  const target = beyondZone.sort((a, b) => direction === "up" ? a.price - b.price : b.price - a.price)[0]?.price ?? null;
  const last = sessionBars[sessionBars.length - 1] ?? { index: choch.index };
  const endIndex = Math.min(bars.length - 1, Math.max(choch.index, last.index));
  return {
    from: bars[choch.index]!.time,
    to: bars[endIndex]!.time,
    priceLow: round2(zone.low),
    priceHigh: round2(zone.high),
    stop: round2(manipulation.extreme),
    target: target == null ? null : round2(target),
    outcome: outcomeOf(bars, choch.index, direction, manipulation.extreme, target),
  };
}

function openingRange(bars: readonly LearnBar[], day: string): number | null {
  const rows = bars.filter((bar) => {
    const parts = istParts(bar.time);
    return parts.day === day && parts.minutes >= OPEN && parts.minutes < HOUR;
  });
  if (rows.length === 0) return null;
  return Math.max(...rows.map((bar) => bar.high)) - Math.min(...rows.map((bar) => bar.low));
}

function thresholdFor(bars: readonly LearnBar[], day: string): number | null {
  const days = [...new Set(bars.map((bar) => istParts(bar.time).day))].filter((item) => item < day).sort();
  const ranges: number[] = [];
  for (const earlier of days) {
    const range = openingRange(bars, earlier);
    if (range != null) ranges.push(range);
  }
  const used = ranges.slice(-20);
  if (used.length === 0) return null;
  return used.reduce((sum, value) => sum + value, 0) / used.length;
}

function readSession(
  bars: readonly LearnBar[],
  day: string,
  indexed: { index: number; bar: LearnBar; minutes: number; day: string }[],
  structure: Structure,
  levels: readonly LevelLine[],
  liquidity: readonly Liquidity[],
  finished: boolean,
): AmdSession {
  const rows = indexed.filter((item) => item.day === day && item.minutes >= OPEN && item.minutes < CLOSE);
  const threshold = thresholdFor(bars, day);
  let high = -Infinity;
  let low = Infinity;
  let start = -1;
  let end = -1;
  for (const row of rows) {
    if (start < 0) start = row.index;
    high = Math.max(high, row.bar.high);
    low = Math.min(low, row.bar.low);
    const expanded = threshold != null && high - low > threshold;
    if (expanded || row.minutes >= HOUR) {
      end = row.index;
      break;
    }
  }
  const accumulation = start >= 0 && end >= 0
    ? { from: bars[start]!.time, to: bars[end]!.time, high, low, endIndex: end }
    : null;
  const pool: Liquidity[] = [];
  if (accumulation) {
    pool.push(
      { id: "accum-high", tag: "BSL", name: "Accum high", price: accumulation.high, side: "high", knownAt: accumulation.endIndex + 1 },
      { id: "accum-low", tag: "SSL", name: "Accum low", price: accumulation.low, side: "low", knownAt: accumulation.endIndex + 1 },
    );
  }
  for (const id of ["pdh", "pdl"] as const) {
    const level = levels.find((item) => item.id === id);
    const ref = REFERENCE[id]!;
    if (level) pool.push({ id, tag: "BSL", name: ref.name, price: level.price, side: ref.side, knownAt: accumulation ? accumulation.endIndex + 1 : 0 });
  }
  const manipulation = accumulation ? firstManipulation(bars, pool, accumulation.endIndex + 1, accumulation.low, accumulation.high) : null;
  let distribution: AmdSession["distribution"] = null;
  if (accumulation && manipulation) {
    const up = manipulation.side === "low";
    const hit = indexed.find((item) => item.day === day && item.index > manipulation.index && (up ? item.bar.close > accumulation.high : item.bar.close < accumulation.low));
    if (hit) {
      const against = up ? "down" : "up";
      const choch = structure.events.find((event) => event.kind === "choch" && event.index > hit.index && event.direction === against);
      const clock = indexed.find((item) => item.day === day && item.index >= hit.index && item.minutes >= FLAT);
      let endIndex = indexed.filter((item) => item.day === day).at(-1)?.index ?? hit.index;
      if (choch && choch.index < bars.length) endIndex = Math.min(endIndex, choch.index);
      if (clock) endIndex = Math.min(endIndex, clock.index);
      distribution = { from: hit.bar.time, to: bars[endIndex]!.time, direction: up ? "up" : "down" };
    }
  }
  const dayDone = finished || indexed.some((item) => item.day === day && item.minutes >= FLAT);
  const note = dayDone && (!manipulation || !distribution) ? "No AMD today" : null;
  const textbook = accumulation && manipulation
    ? textbookFor(bars, rows, manipulation, structure, liquidity, accumulation.high, accumulation.low)
    : null;
  return { day, accumulation, manipulation, distribution, note, textbook };
}

export function readLesson(input: LessonInput): Lesson {
  const bars = input.bars;
  if (bars.length === 0) return { liquidity: [], sweeps: [], sessions: [] };
  const liquidity = [...equalLiquidity(input.structure.swings, bars), ...referenceLiquidity(bars, input.levels)];
  const sweeps = liquidity.flatMap((level) => liquiditySweeps(bars, level));
  const timeframe = input.timeframe ?? "5m";
  const symbol = input.symbol ?? "NIFTY50";
  if (!INTRADAY.has(timeframe) || !AMD_SYMBOLS.has(symbol)) return { liquidity, sweeps, sessions: [] };
  const indexed = bars.map((bar, index) => {
    const parts = istParts(bar.time);
    return { index, bar, minutes: parts.minutes, day: parts.day };
  });
  const days = [...new Set(indexed.map((item) => item.day))];
  const sessions = days.map((day, index) => readSession(
    bars,
    day,
    indexed,
    input.structure,
    input.levels,
    liquidity,
    index < days.length - 1,
  ));
  return {
    liquidity,
    sweeps,
    sessions: sessions.filter((session, index) => index === sessions.length - 1 || session.accumulation != null || session.note != null),
  };
}

export function textbookText(entry: Textbook): string {
  return entry.outcome ? `Textbook entry (learning) · ${entry.outcome}` : "Textbook entry (learning)";
}
