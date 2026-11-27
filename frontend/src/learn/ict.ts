/**
 * ICT model marks for the Learn chart. Definitions follow AurumBoss, in index
 * points, and a mark exists only after the candle that confirms it has closed.
 * IFVG, BPR and the mitigation block stay concepts: they are not drawn.
 */

import { gapFits, sameSession } from "./sanity";
import type { BreakEvent, LearnBar, Swing } from "./structure";
import { istClock, istEpoch } from "./time";
import killzoneFile from "./killzones.json" with { type: "json" };

const FVG_MIN_ATR = 0.1;
const DISP_BODY = 1.2;
const LEG_ATR = 2;
const OB_LOOKBACK = 30;
const PAIR_WINDOW = 150;
const BREAKER_FVG_BARS = 12;

export interface IctBox {
  id: string;
  kind: string;
  t1: number;
  t2: number;
  p1: number;
  p2: number;
  color: string;
  border: string;
  label: string;
  labelColor: string;
  detail: string;
  confirm: number;
  state?: string;
}

export interface IctLine {
  id: string;
  kind: string;
  t1: number;
  t2: number;
  price: number;
  color: string;
  dashed: boolean;
  label: string;
  detail: string;
  confirm: number;
}

export interface IctLabel {
  id: string;
  kind: string;
  time: number;
  price: number;
  text: string;
  detail: string;
  confirm: number;
}

export interface IctEvent {
  time: number;
  kind: string;
  text: string;
  detail: string;
  color: string;
}

export interface Killzone {
  name: string;
  kind: "kz" | "sb";
  t1: number;
  t2: number;
  detail: string;
}

export interface OverlapZone {
  up: boolean;
  top: number;
  bottom: number;
  known: number;
  end: number | null;
}

export interface ModelHit {
  setup: "UNI" | "OTE";
  side: "buy" | "sell";
  index: number;
  slLevel: number;
  why: string;
}

export interface IctScan {
  boxes: IctBox[];
  lines: IctLine[];
  labels: IctLabel[];
  killzones: Killzone[];
  events: IctEvent[];
  overlaps: OverlapZone[];
  hits: ModelHit[];
}

interface ZoneFile {
  note: string;
  zones: { name: string; kind: "kz" | "sb"; start: string; end: string }[];
}

const ZONES = killzoneFile as ZoneFile;

export function killzoneNote(): string {
  return ZONES.note;
}

export function oteBand(lo: number, hi: number, up: boolean): { eq: number; low: number; high: number; sweet: number } {
  const range = hi - lo;
  const eq = lo + range / 2;
  const deep = up ? hi - 0.79 * range : lo + 0.79 * range;
  const shallow = up ? hi - 0.62 * range : lo + 0.62 * range;
  const sweet = up ? hi - 0.705 * range : lo + 0.705 * range;
  return { eq, low: Math.min(deep, shallow), high: Math.max(deep, shallow), sweet };
}

export function tradesZone(zone: OverlapZone, bar: LearnBar, closeAt: number, up: boolean): boolean {
  if (zone.up !== up || zone.known > closeAt) return false;
  if (zone.end != null && zone.end < bar.time) return false;
  return bar.low <= zone.top && bar.high >= zone.bottom;
}

export function killzonesForDay(day: string): Killzone[] {
  const [year, month, date] = day.split("-").map(Number);
  const at = (clock: string) => {
    const [hour, minute] = clock.split(":").map(Number);
    return istEpoch(year ?? 1970, month ?? 1, date ?? 1, hour ?? 0, minute ?? 0);
  };
  return ZONES.zones.map((zone) => ({
    name: zone.name,
    kind: zone.kind,
    t1: at(zone.start),
    t2: at(zone.end),
    detail: `${zone.name} ${zone.start}–${zone.end} IST. ${ZONES.note}`,
  }));
}

function atrAt(bars: readonly LearnBar[], index: number): number {
  let sum = 0;
  const from = Math.max(0, index - 13);
  let count = 0;
  for (let i = from; i <= index; i += 1) {
    const bar = bars[i];
    const prev = i > 0 ? bars[i - 1]?.close ?? bar?.close ?? 0 : bar?.close ?? 0;
    if (!bar) continue;
    const tr = i === 0 ? bar.high - bar.low : Math.max(bar.high, prev) - Math.min(bar.low, prev);
    sum += tr;
    count += 1;
  }
  return count ? sum / count : 0;
}

function round(price: number): string {
  return Number.isInteger(price) ? String(price) : price.toFixed(2);
}

interface Gap {
  up: boolean;
  top: number;
  bottom: number;
  born: number;
  known: number;
  t0: number;
  end: number | null;
}

interface Block {
  up: boolean;
  top: number;
  bottom: number;
  born: number;
  known: number;
  t1: number;
  end: number | null;
  endTime: number | null;
}

export function scanIct(
  bars: readonly LearnBar[],
  events: readonly BreakEvent[],
  swings: readonly Swing[],
  draw: boolean,
): IctScan {
  let step = Number.POSITIVE_INFINITY;
  for (let i = 1; i < bars.length; i += 1) {
    const delta = bars[i]!.time - bars[i - 1]!.time;
    if (delta > 0 && delta < step) step = delta;
  }
  if (!Number.isFinite(step)) step = 300;
  const closeAt = (index: number) => (bars[index]?.time ?? 0) + step;
  const atr = bars.map((_, index) => atrAt(bars, index));
  const gaps: Gap[] = [];
  const openGaps: Gap[] = [];
  for (let j = 0; j < bars.length; j += 1) {
    const bar = bars[j]!;
    for (let k = openGaps.length - 1; k >= 0; k -= 1) {
      const gap = openGaps[k]!;
      if (gap.born >= j) continue;
      if (gap.up ? bar.low <= gap.bottom : bar.high >= gap.top) {
        gap.end = bar.time;
        openGaps.splice(k, 1);
      }
    }
    if (j < 2) continue;
    const left = bars[j - 2];
    const unit = atr[j] ?? 0;
    if (!left || unit <= 0) continue;
    const upGap = bar.low - left.high;
    const downGap = left.low - bar.high;
    let up = false;
    let top = 0;
    let bottom = 0;
    if (upGap >= FVG_MIN_ATR * unit) {
      up = true;
      top = bar.low;
      bottom = left.high;
    } else if (downGap >= FVG_MIN_ATR * unit) {
      top = left.low;
      bottom = bar.high;
    } else continue;
    if (!gapFits(bars, j - 2, j, top, bottom)) continue;
    const gap: Gap = { up, top, bottom, born: j, known: closeAt(j), t0: left.time, end: null };
    gaps.push(gap);
    openGaps.push(gap);
    if (openGaps.length > 150) openGaps.shift();
  }

  const blocks: Block[] = [];
  for (const event of events) {
    if (event.index < 0 || event.index >= bars.length) continue;
    const up = event.direction === "up";
    for (let q = event.index - 1; q >= Math.max(0, event.index - OB_LOOKBACK); q -= 1) {
      const candle = bars[q];
      if (!candle) continue;
      const open = candle.open ?? candle.close;
      if (up ? candle.close >= open : candle.close <= open) continue;
      if (!gapFits(bars, q, q, candle.high, candle.low)) continue;
      const breakBar = bars[event.index];
      if (breakBar && !sameSession(candle.time, breakBar.time)) continue;
      let end: number | null = null;
      for (let k = event.index + 1; k < bars.length; k += 1) {
        const later = bars[k]!;
        if ((up && later.close < candle.low) || (!up && later.close > candle.high)) {
          end = later.time;
          break;
        }
      }
      blocks.push({
        up, top: candle.high, bottom: candle.low, born: event.index, known: closeAt(event.index),
        t1: candle.time, end, endTime: end == null ? null : end + step,
      });
      break;
    }
  }

  const overlaps: OverlapZone[] = [];
  const boxes: IctBox[] = [];
  const lines: IctLine[] = [];
  const labels: IctLabel[] = [];
  const ictEvents: IctEvent[] = [];
  const hits: ModelHit[] = [];

  for (const ob of blocks) {
    for (const gap of gaps) {
      if (gap.up !== ob.up || Math.abs(gap.born - ob.born) > PAIR_WINDOW) continue;
      const lo = Math.max(ob.bottom, gap.bottom);
      const hi = Math.min(ob.top, gap.top);
      if (hi <= lo) continue;
      const unit = atr[Math.min(gap.born, atr.length - 1)] ?? 0;
      if (unit > 0 && hi - lo > 1.5 * unit) continue;
      const known = Math.max(ob.known, gap.known);
      const ends = [ob.end, gap.end].filter((value): value is number => value != null);
      const end = ends.length ? Math.min(...ends) : null;
      if (end != null && end + step <= known) continue;
      overlaps.push({ up: ob.up, top: hi, bottom: lo, known, end });
      if (draw) {
        boxes.push(box(
          `ict-${ob.t1}-${gap.known}`,
          ob.up ? "ict_bull" : "ict_bear",
          Math.max(ob.t1, gap.t0),
          end ?? bars[bars.length - 1]?.time ?? known,
          hi, lo, known, end ? "done" : "active",
          "★ FVG",
          "rgba(219,39,119,0.16)",
          end ? "#6b7280" : "#db2777",
          `${ob.up ? "Bullish" : "Bearish"} OB+FVG ${round(lo)}–${round(hi)}. The order block and the gap overlap.`,
        ));
        ictEvents.push({
          time: known, kind: ob.up ? "ict_bull" : "ict_bear", color: "#a16207",
          text: "OB+FVG overlap",
          detail: `${ob.up ? "Bullish" : "Bearish"} overlap ${round(lo)}–${round(hi)}`,
        });
      }
    }
  }

  const breakers: (Block & { flip: boolean })[] = [];
  for (const ob of blocks) {
    if (ob.end == null) continue;
    const up = !ob.up;
    const born = bars.findIndex((bar) => bar.time === ob.end);
    if (born < 0) continue;
    let end: number | null = null;
    for (let k = born + 1; k < bars.length; k += 1) {
      const later = bars[k]!;
      if ((up && later.close < ob.bottom) || (!up && later.close > ob.top)) {
        end = later.time;
        break;
      }
    }
    const known = closeAt(born);
    breakers.push({ ...ob, up, born, known, end, endTime: end == null ? null : end + step, flip: true });
    if (draw) {
      boxes.push(box(
        `brk-${ob.t1}-${known}`,
        up ? "brk_bull" : "brk_bear",
        ob.end, end ?? bars[bars.length - 1]?.time ?? known,
        ob.top, ob.bottom, known, end ? "done" : "active",
        "Breaker",
        up ? "rgba(20,184,166,0.16)" : "rgba(217,70,239,0.14)",
        end ? "#6b7280" : up ? "#0f766e" : "#a21caf",
        `${up ? "Bullish" : "Bearish"} breaker ${round(ob.bottom)}–${round(ob.top)}. A failed order block that flipped.`,
      ));
    }
  }

  const usedUni = new Set<string>();
  for (const bk of breakers) {
    for (const gap of gaps) {
      if (gap.up !== bk.up) continue;
      if (gap.born < bk.born - BREAKER_FVG_BARS || gap.born > bk.born + BREAKER_FVG_BARS) continue;
      const lo = Math.max(bk.bottom, gap.bottom);
      const hi = Math.min(bk.top, gap.top);
      if (hi <= lo) continue;
      const unit = atr[Math.min(gap.born, atr.length - 1)] ?? 0;
      if (unit > 0 && hi - lo > 1.5 * unit) continue;
      const known = Math.max(bk.known, gap.known);
      const ends = [bk.end, gap.end].filter((value): value is number => value != null);
      const end = ends.length ? Math.min(...ends) : null;
      if (end != null && end + step <= known) continue;
      if (draw) {
        boxes.push(box(
          `uni-${bk.t1}-${gap.known}`,
          bk.up ? "uni_bull" : "uni_bear",
          Math.min(bk.t1, gap.t0),
          end ?? bars[bars.length - 1]?.time ?? known,
          hi, lo, known, end ? "done" : "active",
          "🦄 Unicorn",
          "rgba(236,72,153,0.24)",
          end ? "#6b7280" : "#be185d",
          `${bk.up ? "Bullish" : "Bearish"} Unicorn ${round(lo)}–${round(hi)}. Breaker and fair value gap overlap.`,
        ));
        ictEvents.push({
          time: known, kind: bk.up ? "uni_bull" : "uni_bear", color: "#be185d",
          text: "Unicorn formed",
          detail: `${bk.up ? "Bullish" : "Bearish"} Unicorn ${round(lo)}–${round(hi)}`,
        });
      }
      for (let i = Math.max(bk.born, gap.born) + 1; i < bars.length; i += 1) {
        const bar = bars[i]!;
        if (end != null && bar.time > end) break;
        const closed = closeAt(i);
        if (closed < known) continue;
        const key = `UNI-${istClock(bar.time).day}`;
        if (usedUni.has(key)) break;
        const inside = bar.low <= hi && bar.high >= lo;
        const through = bk.up ? bar.close < lo : bar.close > hi;
        if (through) break;
        if (!inside || !rejection(bar, bk.up)) continue;
        usedUni.add(key);
        hits.push({
          setup: "UNI",
          side: bk.up ? "buy" : "sell",
          index: i,
          slLevel: bk.up ? Math.min(lo, bar.low) : Math.max(hi, bar.high),
          why: `${bk.up ? "Bullish" : "Bearish"} Unicorn ${round(lo)}–${round(hi)}: rejection inside the breaker and the gap. SL beyond the overlap. TP 2R.`,
        });
        break;
      }
    }
  }

  const mssSeen = new Set<number>();
  if (draw) {
    for (const event of events) {
      if (event.kind !== "choch" || mssSeen.has(event.index)) continue;
      const bar = bars[event.index];
      const unit = atrAt(bars, event.index);
      if (!bar || unit <= 0) continue;
      const open = bar.open ?? bar.close;
      if (Math.abs(bar.close - open) < DISP_BODY * unit) continue;
      mssSeen.add(event.index);
      labels.push({
        id: `mss-${event.time}`,
        kind: "mss",
        time: bar.time,
        price: event.direction === "up" ? bar.high : bar.low,
        text: event.direction === "up" ? "MSS ↑" : "MSS ↓",
        detail: `Market structure shift. The CHoCH candle body is at least 1.2 × ATR (${round(unit)}).`,
        confirm: closeAt(event.index),
      });
    }
  }

  const hitsOte = oteHits(bars, events, swings, step, draw, boxes, lines, ictEvents);
  hits.push(...hitsOte);

  const gapBoxes = draw ? openingGapBoxes(bars, step, ictEvents) : [];
  const days = draw ? [...new Set(bars.map((bar) => istClock(bar.time).day))] : [];
  return {
    boxes: [...boxes, ...gapBoxes],
    lines,
    labels,
    killzones: days.flatMap((day) => killzonesForDay(day)),
    events: ictEvents,
    overlaps,
    hits,
  };
}

function oteHits(
  bars: readonly LearnBar[],
  events: readonly BreakEvent[],
  swings: readonly Swing[],
  step: number,
  draw: boolean,
  boxes: IctBox[],
  lines: IctLine[],
  ictEvents: IctEvent[],
): ModelHit[] {
  const hits: ModelHit[] = [];
  const used = new Set<string>();
  let trend: "up" | "down" | null = null;
  let eventAt = 0;
  const ordered = [...events].sort((a, b) => a.index - b.index);
  const confirmed = [...swings].sort((a, b) => a.confirmIndex - b.confirmIndex);
  let swingAt = 0;
  let anchor: Swing | null = null;
  let extreme = 0;
  let extremeIndex = -1;
  let entered = false;
  let picture: { anchor: Swing; lo: number; hi: number; band: ReturnType<typeof oteBand>; up: boolean; confirm: number } | null = null;
  const closeAt = (index: number) => (bars[index]?.time ?? 0) + step;

  for (let i = 0; i < bars.length; i += 1) {
    while (eventAt < ordered.length && ordered[eventAt]!.index === i) {
      trend = ordered[eventAt]!.direction;
      eventAt += 1;
    }
    while (swingAt < confirmed.length && confirmed[swingAt]!.confirmIndex === i) {
      const swing = confirmed[swingAt]!;
      swingAt += 1;
      if (trend === "up" && swing.kind === "low") {
        anchor = swing;
        extreme = swing.price;
        extremeIndex = swing.index;
        entered = false;
      } else if (trend === "down" && swing.kind === "high") {
        anchor = swing;
        extreme = swing.price;
        extremeIndex = swing.index;
        entered = false;
      }
    }
    const bar = bars[i]!;
    if (!anchor || !trend || i <= anchor.index) continue;
    if (trend === "up") {
      if (bar.high >= extreme) {
        extreme = bar.high;
        extremeIndex = i;
        entered = false;
        const span = extreme - anchor.price;
        const unitNow = atrAt(bars, i);
        if (span >= LEG_ATR * unitNow) {
          picture = { anchor, lo: anchor.price, hi: extreme, band: oteBand(anchor.price, extreme, true), up: true, confirm: closeAt(i) };
        }
        continue;
      }
    } else if (bar.low <= extreme) {
      extreme = bar.low;
      extremeIndex = i;
      entered = false;
      const span = anchor.price - extreme;
      const unitNow = atrAt(bars, i);
      if (span >= LEG_ATR * unitNow) {
        picture = { anchor, lo: extreme, hi: anchor.price, band: oteBand(extreme, anchor.price, false), up: false, confirm: closeAt(i) };
      }
      continue;
    }
    const lo = trend === "up" ? anchor.price : extreme;
    const hi = trend === "up" ? extreme : anchor.price;
    const unit = atrAt(bars, extremeIndex);
    if (hi - lo < LEG_ATR * unit || extremeIndex >= i) continue;
    const band = oteBand(lo, hi, trend === "up");
    if (hi - lo >= LEG_ATR * unit) {
      picture = { anchor, lo, hi, band, up: trend === "up", confirm: closeAt(extremeIndex) };
    }
    if (extremeIndex >= i) continue;
    const inside = bar.low <= band.high && bar.high >= band.low;
    if (inside && !entered) {
      entered = true;
      ictEvents.push({
        time: closeAt(i), kind: "ote", color: "#2563eb",
        text: "Price entered OTE",
        detail: `OTE ${round(band.low)}–${round(band.high)} of ${round(lo)}–${round(hi)}`,
      });
    }
    const day = istClock(bar.time).day;
    const key = `OTE-${day}`;
    if (!used.has(key) && inside && rejection(bar, trend === "up") && i > extremeIndex) {
      used.add(key);
      hits.push({
        setup: "OTE",
        side: trend === "up" ? "buy" : "sell",
        index: i,
        slLevel: trend === "up" ? Math.min(band.low, bar.low) : Math.max(band.high, bar.high),
        why: `OTE ${trend === "up" ? "buy" : "sell"} ${round(band.low)}–${round(band.high)} of the leg ${round(lo)}–${round(hi)}. SL beyond the zone. TP 2R.`,
      });
    }
  }
  if (draw && picture) {
    const last = bars[bars.length - 1]?.time ?? picture.anchor.time;
    const { lo, hi, band, up, confirm } = picture;
    boxes.push(box(
      `prem-${picture.anchor.time}`, "premium", picture.anchor.time, last, hi, band.eq, confirm, "active",
      "Premium", "rgba(220,38,38,0.06)", "rgba(220,38,38,0.0)",
      `Premium, above EQ ${round(band.eq)} of ${round(lo)}–${round(hi)}.`,
    ));
    boxes.push(box(
      `disc-${picture.anchor.time}`, "discount", picture.anchor.time, last, band.eq, lo, confirm, "active",
      "Discount", "rgba(22,163,74,0.06)", "rgba(22,163,74,0.0)",
      `Discount, below EQ ${round(band.eq)} of ${round(lo)}–${round(hi)}.`,
    ));
    boxes.push(box(
      `ote-${picture.anchor.time}`, "ote", picture.anchor.time, last, band.high, band.low, confirm, "active",
      up ? "OTE buy 62–79%" : "OTE sell 62–79%",
      "rgba(59,130,246,0.12)", "#2563eb",
      `OTE ${round(band.low)}–${round(band.high)}. Sweet spot 70.5% is ${round(band.sweet)}.`,
    ));
    lines.push({
      id: `eq-${picture.anchor.time}`, kind: "eq", t1: picture.anchor.time, t2: last, price: band.eq,
      color: "#2563eb", dashed: true, label: "EQ 50%",
      detail: `Equilibrium ${round(band.eq)}. Above is premium. Below is discount.`,
      confirm,
    });
  }
  return hits;
}

function openingGapBoxes(bars: readonly LearnBar[], step: number, ictEvents: IctEvent[]): IctBox[] {
  const byDay = new Map<string, LearnBar[]>();
  for (const bar of bars) {
    const day = istClock(bar.time).day;
    const list = byDay.get(day);
    if (list) list.push(bar);
    else byDay.set(day, [bar]);
  }
  const days = [...byDay.keys()].sort();
  const out: IctBox[] = [];
  for (let i = 1; i < days.length; i += 1) {
    const prev = byDay.get(days[i - 1]!)!;
    const today = byDay.get(days[i]!)!;
    const prevClose = prev[prev.length - 1]!.close;
    const first = today[0]!;
    const open = first.open ?? first.close;
    if (prevClose === open) continue;
    const prevWeekday = istClock(prev[0]!.time).weekday;
    const week = prevWeekday === 5;
    const top = Math.max(prevClose, open);
    const bottom = Math.min(prevClose, open);
    let filled: number | null = null;
    for (const bar of today.slice(1)) {
      const back = open > prevClose ? bar.low <= prevClose : bar.high >= prevClose;
      if (back) {
        filled = bar.time;
        break;
      }
    }
    const known = first.time + step;
    const kind = week ? "nwog" : "ndog";
    out.push(box(
      `${kind}-${days[i]}`,
      kind,
      first.time,
      filled ?? today[today.length - 1]!.time,
      top, bottom, known, filled ? "filled" : "open",
      week ? "NWOG" : "NDOG",
      filled ? "rgba(156,163,175,0.12)" : "rgba(14,165,233,0.16)",
      filled ? "#6b7280" : "#0369a1",
      `${week ? "New week" : "New day"} opening gap ${round(bottom)}–${round(top)}. ${filled ? "Filled." : "Open."}`,
    ));
    if (filled) {
      ictEvents.push({
        time: filled + step, kind, color: "#0369a1",
        text: week ? "NWOG filled" : "NDOG filled",
        detail: `Price traded back to the prior close ${round(prevClose)}.`,
      });
    }
  }
  return out;
}

function rejection(bar: LearnBar, up: boolean): boolean {
  const open = bar.open ?? bar.close;
  const range = bar.high - bar.low;
  if (range <= 0) return false;
  const bodyHigh = Math.max(open, bar.close);
  if (up) return bar.close > open && (Math.min(open, bar.close) - bar.low >= 0.5 * range || bar.close >= bar.low + 0.75 * range);
  return bar.close < open && (bar.high - bodyHigh >= 0.5 * range || bar.close <= bar.high - 0.75 * range);
}

function box(
  id: string, kind: string, t1: number, t2: number, p1: number, p2: number, confirm: number, state: string,
  label: string, color: string, border: string, detail: string,
): IctBox {
  return { id, kind, t1, t2, p1, p2, confirm, state, label, labelColor: border, color, border, detail };
}

export const ICT_TERMS: { term: string; what: string; chart: string; focus: string }[] = [
  { term: "Power of 3 (AMD)", what: "The session has three phases: accumulation (the opening balance), manipulation (a false move that takes liquidity), and distribution (the real move).", chart: "AMD layer: the opening-balance box, the manipulation label, and the distribution label.", focus: "amd" },
  { term: "Judas Swing", what: "The false move near the open that runs stops the wrong way before the real move.", chart: "The manipulation label, a sweep that closed back inside.", focus: "amd_m" },
  { term: "Liquidity (buy-side/sell-side)", what: "Stops sit above highs (buy-side) and below lows (sell-side). Price is drawn to them.", chart: "PDH, PDL, the previous week, equal highs and lows, and swept labels.", focus: "liq" },
  { term: "BOS/CHoCH", what: "Break of structure continues the trend. Change of character is the first close against it. A candle close is required.", chart: "BOS and CHoCH tags.", focus: "bos" },
  { term: "Displacement & MSS", what: "Displacement is a wide-bodied candle. A change of character whose body is at least 1.2 × ATR is a market structure shift.", chart: "An MSS tag next to that CHoCH. A slow close stays a CHoCH.", focus: "mss" },
  { term: "FVG", what: "A three-candle imbalance. Price often returns to the gap between candle 1 and candle 3.", chart: "Green and red fair value gap boxes.", focus: "fvg" },
  { term: "Order Block", what: "The last opposite candle before a move that broke structure.", chart: "Order block boxes. The layer is off until you switch it on.", focus: "ob" },
  { term: "OB + FVG confluence", what: "An order block and a fair value gap of the same direction overlap. That overlap is the point of interest.", chart: "★ OB+FVG boxes.", focus: "ict" },
  { term: "Breaker block", what: "An order block that failed. Price closed through it, and the zone flips.", chart: "Breaker boxes. Teal is bullish, magenta is bearish.", focus: "brk" },
  { term: "Unicorn model", what: "A breaker overlapping a fair value gap of the same direction.", chart: "Pink 🦄 Unicorn boxes.", focus: "uni" },
  { term: "Premium/Discount/Equilibrium", what: "Above 50% of the current leg is premium. Below 50% is discount.", chart: "A dashed EQ 50% line, with premium shaded above it and discount below it.", focus: "eq" },
  { term: "OTE", what: "The 62–79% retracement of the current leg. 70.5% is the sweet spot.", chart: "The blue OTE box on that leg.", focus: "ote" },
  { term: "Killzones", what: "The busier parts of the NSE session. These windows are adapted for NSE, not the New York clock.", chart: "A strip along the bottom: Opening killzone 09:15–10:30 and Closing 14:30–15:15.", focus: "kz" },
  { term: "Silver Bullet", what: "Two one-hour windows, adapted for NSE, used to watch a return into a fair value gap toward liquidity.", chart: "Silver Bullet (NSE) 10:00–11:00 and 13:30–14:30, on the same strip.", focus: "sb" },
  { term: "ICT 2022 Mentorship model", what: "Liquidity is swept, structure shifts with displacement, and the entry is the return into the fair value gap, aimed at the opposite liquidity.", chart: "not drawn — concept only. The sweep, the MSS tag, and the fair value gap are the pieces.", focus: "" },
  { term: "Inversion FVG (IFVG)", what: "A fair value gap that price closed through. It is then read in the other direction.", chart: "not drawn — concept only.", focus: "" },
  { term: "Balanced Price Range (BPR)", what: "The overlap of a bullish fair value gap and a bearish fair value gap.", chart: "not drawn — concept only.", focus: "" },
  { term: "Mitigation block", what: "Like a breaker, formed when the swing did not take liquidity first.", chart: "not drawn — concept only.", focus: "" },
  { term: "NWOG/NDOG", what: "The gap from the previous close to the next open. A Friday close into the next week's open is the week gap. Other opens are the day gap. Price often trades back to the prior close.", chart: "NDOG and NWOG boxes. A filled gap stays visible when the filled toggle is on.", focus: "ndog" },
];
