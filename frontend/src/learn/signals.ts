/**
 * Watch-only Learn signals. Nothing here places a trade or touches a paper slot.
 * Setups are read from closed 5-minute candles. A bar is known at its close
 * (start + 5 minutes). Replay passes only the candles closed by the cursor.
 */

import { atrAt, readLesson, trueRanges, type Lesson } from "./lesson";
import { readLevels } from "./levels";
import { gapSeeds, type StudyBox } from "./study";
import { readStructure, type BreakEvent, type LearnBar, type Structure } from "./structure";
import { isExpiryDay } from "./events";
import { scanIct, tradesZone, type OverlapZone } from "./ict";
import { istClock, istEpoch } from "./time";

export const M5 = 300;
export const RR = 2;
const SL_BUFFER = 0.1;
const SWEEP_WINDOW = 60 * 60;
const LAST_ENTRY = 15 * 60;
const EXPIRE = 15 * 60 + 15;
const FVG_BARS = 150;

const LEVELS = new Set(["PDH", "PDL", "Week high", "Week low", "OR high", "OR low", "EQH", "EQL"]);

export type SetupName = "SWP" | "FVG" | "AMD" | "UNI" | "OTE";
export type SignalSide = "buy" | "sell";
export type SignalStatus = "open" | "TP" | "SL" | "expired";
export type PlanState = "waiting" | "in_zone";
export type SessionSlot = "opening" | "midday" | "closing";
export type TrendAlign = "with" | "against" | "flat";
export type LevelGroup = "PDH/PDL" | "week" | "OR" | "EQH/EQL" | "other";

export const OPTION_COST_INR = 110;
const LOT: Record<string, number> = { NIFTY50: 65, SENSEX: 20, BANKNIFTY: 30 };

export interface WatchSignal {
  id: string;
  symbol: string;
  setup: SetupName;
  side: SignalSide;
  time: number;
  entry: number;
  sl: number;
  tp: number;
  risk: number;
  tpNote: string;
  why: string;
  status: SignalStatus;
  exitTime: number | null;
  exit: number | null;
  r: number | null;
  liveR: number | null;
  slot: SessionSlot;
  trend15: TrendAlign;
  level: LevelGroup;
  expiry: boolean;
  costR: number;
  star: boolean;
}

export interface PlanCard {
  n: number;
  setup: SetupName;
  side: SignalSide;
  title: string;
  trigger: string;
  invalid: string;
  zone: [number, number];
  entry: number;
  sl: number;
  tp: number;
  approx: boolean;
  distance: number;
  state: PlanState;
  tpNote: string;
}

export interface PlanDone {
  setup: SetupName;
  side: SignalSide;
  title: string;
  text: string;
  result: "signal" | "cancelled" | "not_counted";
  time: number;
}

export interface SetupStats {
  signals: number;
  tp: number;
  sl: number;
  expired: number;
  open: number;
  winRate: number | null;
  totalR: number;
  avgR: number | null;
  avgRNet: number | null;
}

export interface BreakdownRow {
  setup: SetupName;
  slot: SessionSlot;
  trend15: TrendAlign;
  level: LevelGroup;
  expiry: boolean;
  n: number;
  winRate: number | null;
  avgR: number | null;
  avgRNet: number | null;
  verdict: string;
}

export interface SignalBook {
  plans: PlanCard[];
  done: PlanDone[];
  notes: string[];
  signals: WatchSignal[];
  stats: Record<"all" | SetupName | "FVG★" | "FVG·", SetupStats>;
  trend: "up" | "down" | null;
  price: number | null;
  breakdown: BreakdownRow[];
}

export function emptyStats(): SetupStats {
  return { signals: 0, tp: 0, sl: 0, expired: 0, open: 0, winRate: null, totalR: 0, avgR: null, avgRNet: null };
}

export function emptyBook(): SignalBook {
  return {
    plans: [],
    done: [],
    notes: [],
    signals: [],
    stats: {
      all: emptyStats(), SWP: emptyStats(), FVG: emptyStats(), AMD: emptyStats(),
      UNI: emptyStats(), OTE: emptyStats(), "FVG★": emptyStats(), "FVG·": emptyStats(),
    },
    trend: null,
    price: null,
    breakdown: [],
  };
}

export function closeOf(bar: LearnBar): number {
  return bar.time + M5;
}

export function entryAllowed(closeAt: number): boolean {
  const clock = istClock(closeAt);
  if (clock.weekday === 0 || clock.weekday === 6) return false;
  return clock.minutes >= 9 * 60 + 15 && clock.minutes <= LAST_ENTRY;
}

export function zoneState(zone: [number, number], price: number): PlanState {
  const low = Math.min(zone[0], zone[1]);
  const high = Math.max(zone[0], zone[1]);
  return price >= low && price <= high ? "in_zone" : "waiting";
}

export function isRejection(bar: LearnBar, up: boolean): boolean {
  const open = bar.open ?? bar.close;
  const range = bar.high - bar.low;
  if (range <= 0) return false;
  const bodyLow = Math.min(open, bar.close);
  const bodyHigh = Math.max(open, bar.close);
  if (up) return bar.close > open && (bodyLow - bar.low >= 0.5 * range || bar.close >= bar.low + 0.75 * range);
  return bar.close < open && (bar.high - bodyHigh >= 0.5 * range || bar.close <= bar.high - 0.75 * range);
}

/** AMD target: the nearer of 2R and the closest opposite liquidity. */
export function amdTarget(
  side: SignalSide,
  entry: number,
  risk: number,
  levels: readonly { name: string; price: number }[],
): { tp: number; note: string } {
  const two = side === "buy" ? entry + RR * risk : entry - RR * risk;
  const opposite = levels
    .filter((level) => (side === "buy" ? level.price > entry : level.price < entry))
    .sort((a, b) => Math.abs(a.price - entry) - Math.abs(b.price - entry));
  const near = opposite[0];
  if (!near) return { tp: round2(two), note: "2R (no opposite liquidity)" };
  if (Math.abs(near.price - entry) <= Math.abs(two - entry)) {
    return { tp: round2(near.price), note: `${near.name} (nearer than 2R)` };
  }
  return { tp: round2(two), note: `2R (nearer than ${near.name})` };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function atrAtBar(bars: readonly LearnBar[], index: number): number {
  const value = atrAt(trueRanges(bars), index);
  if (value != null && value > 0) return value;
  const bar = bars[index];
  return bar ? Math.max(bar.high - bar.low, 0.05) : 0.05;
}

function trendAt(events: readonly BreakEvent[], time: number): "up" | "down" | null {
  let lo = 0;
  let hi = events.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (events[mid]!.time <= time) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found < 0 ? null : events[found]!.direction;
}

function makeSignal(
  setup: SetupName,
  side: SignalSide,
  symbol: string,
  bar: LearnBar,
  slLevel: number,
  atr: number,
  tpOverride: number | null,
  tpNote: string,
  why: string,
): WatchSignal | null {
  const closeAt = closeOf(bar);
  if (!entryAllowed(closeAt)) return null;
  const buf = SL_BUFFER * atr;
  const sl = side === "buy" ? slLevel - buf : slLevel + buf;
  const risk = side === "buy" ? bar.close - sl : sl - bar.close;
  if (risk <= 0) return null;
  const twoR = side === "buy" ? bar.close + RR * risk : bar.close - RR * risk;
  const tp = tpOverride ?? twoR;
  return {
    id: `${setup}-${symbol}-${closeAt}`,
    symbol,
    setup,
    side,
    time: closeAt,
    entry: round2(bar.close),
    sl: round2(sl),
    tp: round2(tp),
    risk: round2(risk),
    tpNote,
    why,
    status: "open",
    exitTime: null,
    exit: null,
    r: null,
    liveR: null,
    slot: "midday",
    trend15: "flat",
    level: "other",
    expiry: false,
    costR: 0,
    star: false,
  };
}

export function scoreSignal(signal: WatchSignal, bars: readonly LearnBar[], now: number): WatchSignal {
  const buy = signal.side === "buy";
  const day = istClock(signal.time).day;
  let last: LearnBar | null = null;
  for (const bar of bars) {
    if (bar.time < signal.time) continue;
    const closed = closeOf(bar);
    if (closed > now) break;
    if ((buy && bar.low <= signal.sl) || (!buy && bar.high >= signal.sl)) {
      return { ...signal, status: "SL", exitTime: closed, exit: signal.sl, r: -1, liveR: null };
    }
    if ((buy && bar.high >= signal.tp) || (!buy && bar.low <= signal.tp)) {
      const reward = signal.risk > 0 ? Math.abs(signal.tp - signal.entry) / signal.risk : RR;
      return { ...signal, status: "TP", exitTime: closed, exit: signal.tp, r: round2(reward), liveR: null };
    }
    last = bar;
    if (istClock(closed).day === day && istClock(closed).minutes >= EXPIRE) {
      const r = buy ? (bar.close - signal.entry) / signal.risk : (signal.entry - bar.close) / signal.risk;
      return { ...signal, status: "expired", exitTime: closed, exit: round2(bar.close), r: round2(r), liveR: null };
    }
  }
  if (!last) return signal;
  const live = buy ? (last.close - signal.entry) / signal.risk : (signal.entry - last.close) / signal.risk;
  return { ...signal, liveR: round2(live) };
}

function broke(side: "high" | "low", bar: LearnBar, level: number): boolean {
  return side === "high" ? bar.close > level : bar.close < level;
}

function swpSignals(bars: readonly LearnBar[], structure: Structure, lesson: Lesson, symbol: string): { signals: WatchSignal[]; done: PlanDone[] } {
  const signals: WatchSignal[] = [];
  const done: PlanDone[] = [];
  const used = new Set<string>();
  const sweeps = lesson.sweeps.filter((sweep) => LEVELS.has(sweep.name)).sort((a, b) => a.time - b.time);
  for (const sweep of sweeps) {
    const day = istClock(sweep.time).day;
    const key = `SWP-${day}`;
    const want = sweep.side === "high" ? "down" : "up";
    const side: SignalSide = want === "up" ? "buy" : "sell";
    const sweepClose = closeOf({ time: sweep.time, high: 0, low: 0, close: 0 });
    const limit = sweepClose + SWEEP_WINDOW;
    const choch = [...structure.events]
      .filter((event) => event.kind === "choch" && event.direction === want)
      .sort((a, b) => a.time - b.time)
      .find((event) => {
        const closed = closeOf({ time: event.time, high: 0, low: 0, close: 0 });
        return closed > sweepClose && closed <= limit && entryAllowed(closed);
      });
    const until = choch ? choch.time : limit;
    const breaking = bars.find((bar) => bar.time > sweep.time && bar.time <= until && closeOf(bar) <= limit && broke(sweep.side, bar, sweep.price));
    if (breaking && (!choch || breaking.time <= choch.time)) {
      done.push({
        setup: "SWP",
        side,
        title: `Sweep of ${sweep.name}`,
        text: `Cancelled ${istClock(closeOf(breaking)).day} ${clockText(closeOf(breaking))} — an M5 candle closed beyond ${sweep.name}`,
        result: "cancelled",
        time: closeOf(breaking),
      });
      continue;
    }
    if (!choch || used.has(key)) {
      if (choch && used.has(key)) {
        done.push({
          setup: "SWP", side, title: `Sweep of ${sweep.name}`,
          text: "Not counted — SWP already has today's one signal",
          result: "not_counted", time: closeOf({ time: choch.time, high: 0, low: 0, close: 0 }),
        });
      }
      continue;
    }
    const bar = bars[choch.index];
    if (!bar) continue;
    const atr = atrAtBar(bars, choch.index);
    const signal = makeSignal(
      "SWP", side, symbol, bar, sweep.extreme, atr, null, "2R",
      `${sweep.name} swept to ${round2(sweep.extreme)}, then CHoCH ${want} on M5. SL beyond the sweep. TP 2R.`,
    );
    if (!signal) continue;
    used.add(key);
    signals.push(signal);
    done.push({ setup: "SWP", side, title: `Sweep of ${sweep.name}`, text: "Triggered — signal", result: "signal", time: signal.time });
  }
  return { signals, done };
}

function gapEdges(gap: StudyBox): { up: boolean; top: number; bottom: number } {
  return { up: gap.kind === "fvg_bull", top: Math.max(gap.p1, gap.p2), bottom: Math.min(gap.p1, gap.p2) };
}

function fvgSignals(
  bars: readonly LearnBar[],
  structure: Structure,
  gaps: readonly StudyBox[],
  symbol: string,
  overlaps: readonly OverlapZone[],
): { signals: WatchSignal[]; done: PlanDone[] } {
  const signals: WatchSignal[] = [];
  const done: PlanDone[] = [];
  const used = new Set<string>();
  for (const gap of gaps) {
    const { up, top, bottom } = gapEdges(gap);
    const side: SignalSide = up ? "buy" : "sell";
    const start = bars.findIndex((bar) => bar.time >= gap.confirm);
    if (start < 0) continue;
    for (let i = start + 1; i < bars.length && i <= start + FVG_BARS; i += 1) {
      const bar = bars[i]!;
      const closed = closeOf(bar);
      const through = up ? bar.close < bottom : bar.close > top;
      if (through) {
        if (trendAt(structure.events, gap.confirm) === (up ? "up" : "down")) {
          done.push({
            setup: "FVG", side, title: `${up ? "Bullish" : "Bearish"} FVG ${round2(bottom)}–${round2(top)}`,
            text: "Cancelled — an M5 candle closed through the gap", result: "cancelled", time: closed,
          });
        }
        break;
      }
      const inside = up ? bar.low <= top : bar.high >= bottom;
      if (!inside || !isRejection(bar, up) || trendAt(structure.events, bar.time) !== (up ? "up" : "down")) continue;
      const day = istClock(bar.time).day;
      const key = `FVG-${day}`;
      if (used.has(key)) {
        done.push({
          setup: "FVG", side, title: `${up ? "Bullish" : "Bearish"} FVG`,
          text: "Not counted — FVG already has today's one signal", result: "not_counted", time: closed,
        });
        break;
      }
      const atr = atrAtBar(bars, i);
      const slLevel = up ? Math.min(bottom, bar.low) : Math.max(top, bar.high);
      const signal = makeSignal(
        "FVG", side, symbol, bar, slLevel, atr, null, "2R",
        `${up ? "Bullish" : "Bearish"} FVG ${round2(bottom)}–${round2(top)}: rejection with the M5 ${up ? "up" : "down"}trend. SL beyond the gap. TP 2R.`,
      );
      if (!signal) continue;
      if (overlaps.some((zone) => tradesZone(zone, bar, signal.time, up))) {
        signal.star = true;
        signal.why += " · inside an OB+FVG zone";
      }
      used.add(key);
      signals.push(signal);
      done.push({ setup: "FVG", side, title: signal.why, text: "Triggered — signal", result: "signal", time: signal.time });
      break;
    }
  }
  return { signals, done };
}

function amdSignals(bars: readonly LearnBar[], structure: Structure, lesson: Lesson, symbol: string): WatchSignal[] {
  const signals: WatchSignal[] = [];
  const used = new Set<string>();
  for (const session of lesson.sessions) {
    const manip = session.manipulation;
    if (!manip) continue;
    const key = `AMD-${session.day}`;
    if (used.has(key)) continue;
    const want = manip.side === "low" ? "up" : "down";
    const choch = [...structure.events]
      .filter((event) => event.kind === "choch" && event.direction === want && event.index > manip.index)
      .sort((a, b) => a.time - b.time)
      .find((event) => istClock(event.time).day === session.day && entryAllowed(closeOf({ time: event.time, high: 0, low: 0, close: 0 })));
    if (!choch) continue;
    const bar = bars[choch.index];
    if (!bar) continue;
    const atr = atrAtBar(bars, choch.index);
    const side: SignalSide = want === "up" ? "buy" : "sell";
    const slLevel = manip.extreme;
    const buf = SL_BUFFER * atr;
    const sl = side === "buy" ? slLevel - buf : slLevel + buf;
    const risk = side === "buy" ? bar.close - sl : sl - bar.close;
    if (risk <= 0) continue;
    const levels = [
      ...lesson.liquidity.filter((level) => level.knownAt <= choch.index).map((level) => ({ name: level.name, price: level.price })),
      ...(session.accumulation
        ? [
            { name: "Opening-balance high", price: session.accumulation.high },
            { name: "Opening-balance low", price: session.accumulation.low },
          ]
        : []),
    ];
    const target = amdTarget(side, bar.close, risk, levels);
    const signal = makeSignal(
      "AMD", side, symbol, bar, manip.extreme, atr, target.tp, target.note,
      `Manipulation swept ${manip.name} to ${round2(manip.extreme)}, then CHoCH ${want}. SL beyond the sweep. TP ${target.note}.`,
    );
    if (!signal) continue;
    used.add(key);
    signals.push(signal);
  }
  return signals;
}

export function tally(signals: readonly WatchSignal[]): Record<"all" | SetupName | "FVG★" | "FVG·", SetupStats> {
  const block = (rows: readonly WatchSignal[]): SetupStats => {
    const done = rows.filter((row) => row.status === "TP" || row.status === "SL" || row.status === "expired");
    const tp = rows.filter((row) => row.status === "TP").length;
    const sl = rows.filter((row) => row.status === "SL").length;
    const expired = rows.filter((row) => row.status === "expired").length;
    const totalR = round2(done.reduce((sum, row) => sum + (row.r ?? 0), 0));
    const net = done.length ? round2(done.reduce((sum, row) => sum + (row.r ?? 0) - row.costR, 0) / done.length) : null;
    return {
      signals: rows.length,
      tp,
      sl,
      expired,
      open: rows.length - done.length,
      winRate: tp + sl ? round2((100 * tp) / (tp + sl)) : null,
      totalR,
      avgR: done.length ? round2(totalR / done.length) : null,
      avgRNet: net,
    };
  };
  return {
    all: block(signals),
    SWP: block(signals.filter((row) => row.setup === "SWP")),
    FVG: block(signals.filter((row) => row.setup === "FVG")),
    AMD: block(signals.filter((row) => row.setup === "AMD")),
    UNI: block(signals.filter((row) => row.setup === "UNI")),
    OTE: block(signals.filter((row) => row.setup === "OTE")),
    "FVG★": block(signals.filter((row) => row.setup === "FVG" && row.star)),
    "FVG·": block(signals.filter((row) => row.setup === "FVG" && !row.star)),
  };
}

function clockText(epoch: number): string {
  const minutes = istClock(epoch).minutes;
  const hour = Math.floor(minutes / 60);
  const minute = minutes % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function card(
  side: SignalSide,
  setup: SetupName,
  title: string,
  trigger: string,
  zone: [number, number],
  entry: number,
  sl: number,
  invalid: string,
  price: number,
  approx: boolean,
  tpNote: string,
): PlanCard {
  const risk = Math.abs(entry - sl);
  const tp = side === "buy" ? entry + RR * risk : entry - RR * risk;
  const mid = (zone[0] + zone[1]) / 2;
  return {
    n: 0,
    setup,
    side,
    title,
    trigger,
    invalid,
    zone: [round2(zone[0]), round2(zone[1])],
    entry: round2(entry),
    sl: round2(sl),
    tp: round2(tp),
    approx,
    distance: round2(mid - price),
    state: zoneState(zone, price),
    tpNote,
  };
}

function buildPlans(
  bars: readonly LearnBar[],
  structure: Structure,
  lesson: Lesson,
  gaps: readonly StudyBox[],
  signals: readonly WatchSignal[],
  now: number,
  price: number,
  atr: number,
): { plans: PlanCard[]; notes: string[] } {
  const today = istClock(now).day;
  const notes: string[] = [];
  const plans: PlanCard[] = [];
  const used = new Set(signals.filter((signal) => istClock(signal.time).day === today).map((signal) => signal.setup));
  const trend = trendAt(structure.events, bars[bars.length - 1]?.time ?? now);
  notes.push(`M5 trend: ${trend ?? "unclear"} · price ${round2(price)}`);
  for (const setup of used) {
    const fired = signals.find((signal) => signal.setup === setup && istClock(signal.time).day === today);
    if (fired) notes.push(`${setup}: today's signal already fired — max 1 a day.`);
  }
  const session = lesson.sessions.find((item) => item.day === today) ?? null;
  if (!used.has("AMD")) {
    if (!session || !session.accumulation) {
      notes.push("AMD: opening balance still forming — the plan appears once 10:15 passes or the range expands.");
    } else if (!session.manipulation) {
      const { high, low } = session.accumulation;
      plans.push(card("sell", "AMD", `Sweep of the opening-balance high ${round2(high)}`,
        `Price trades above ${round2(high)}, an M5 candle closes back below it, then the first bearish CHoCH → SELL on that close.`,
        [high, high + 0.5 * atr], high, high + 0.6 * atr,
        `Cancelled if an M5 candle closes above ${round2(high)} and holds outside the balance.`, price, true, "2R or opposite liquidity, whichever is nearer"));
      plans.push(card("buy", "AMD", `Sweep of the opening-balance low ${round2(low)}`,
        `Price trades below ${round2(low)}, an M5 candle closes back above it, then the first bullish CHoCH → BUY on that close.`,
        [low - 0.5 * atr, low], low, low - 0.6 * atr,
        `Cancelled if an M5 candle closes below ${round2(low)} and holds outside the balance.`, price, true, "2R or opposite liquidity, whichever is nearer"));
    } else {
      const manip = session.manipulation;
      const up = manip.side === "low";
      const sl = up ? manip.extreme - SL_BUFFER * atr : manip.extreme + SL_BUFFER * atr;
      plans.push(card(up ? "buy" : "sell", "AMD", `Opening balance ${manip.side} already swept to ${round2(manip.extreme)}`,
        `Watching each M5 close for the first ${up ? "bullish" : "bearish"} CHoCH → ${up ? "BUY" : "SELL"} on that close.`,
        [Math.min(manip.extreme, price), Math.max(manip.extreme, price)], price, sl,
        `Cancelled if an M5 candle closes back beyond ${round2(manip.extreme)}.`, price, false, "2R or opposite liquidity, whichever is nearer"));
      plans[plans.length - 1]!.state = "in_zone";
    }
  }
  if (!used.has("FVG")) {
    const open = gaps.filter((gap) => gap.state === "open" || gap.state === "tested");
    for (const up of [true, false]) {
      const want = up ? "up" : "down";
      const candidates = open.filter((gap) => gap.kind === (up ? "fvg_bull" : "fvg_bear"));
      candidates.sort((a, b) => Math.abs((a.p1 + a.p2) / 2 - price) - Math.abs((b.p1 + b.p2) / 2 - price));
      const gap = candidates[0];
      if (!gap) continue;
      const { top, bottom } = gapEdges(gap);
      if (trend !== want) {
        notes.push(`FVG: ${up ? "bullish" : "bearish"} gap ${round2(bottom)}–${round2(top)} is against the M5 trend — no plan.`);
        continue;
      }
      const sl = up ? bottom - SL_BUFFER * atr : top + SL_BUFFER * atr;
      plans.push(card(up ? "buy" : "sell", "FVG", `Rejection at the ${up ? "bullish" : "bearish"} FVG ${round2(bottom)}–${round2(top)}`,
        `Price ${up ? "dips into" : "rallies into"} the gap and an M5 ${up ? "bullish" : "bearish"} rejection candle closes there → ${up ? "BUY" : "SELL"} on that close.`,
        [bottom, top], up ? top : bottom, sl,
        `Cancelled if an M5 candle closes ${up ? "below" : "above"} ${round2(up ? bottom : top)}, or the M5 trend turns.`,
        price, false, "2R"));
    }
  }
  if (!used.has("SWP")) {
    const waiting = lesson.sweeps
      .filter((sweep) => LEVELS.has(sweep.name) && istClock(sweep.time).day === today)
      .sort((a, b) => b.time - a.time)[0];
    const already = signals.some((signal) => signal.setup === "SWP" && istClock(signal.time).day === today);
    if (waiting && !already && closeOf({ time: waiting.time, high: 0, low: 0, close: 0 }) + SWEEP_WINDOW >= now) {
      const up = waiting.side === "low";
      const sl = up ? waiting.extreme - SL_BUFFER * atr : waiting.extreme + SL_BUFFER * atr;
      plans.push(card(up ? "buy" : "sell", "SWP", `Sweep of ${waiting.name} closed back inside`,
        `Watching each M5 close for a ${up ? "bullish" : "bearish"} CHoCH within 1 hour → ${up ? "BUY" : "SELL"} on that close.`,
        [Math.min(waiting.price, price), Math.max(waiting.price, price)], price, sl,
        `Cancelled if an M5 candle closes beyond ${waiting.name} at ${round2(waiting.price)}.`,
        price, false, "2R"));
      plans[plans.length - 1]!.state = "in_zone";
    } else if (!waiting) {
      const live = lesson.liquidity.filter((level) => LEVELS.has(level.name));
      const above = live.filter((level) => level.price > price).sort((a, b) => a.price - b.price)[0];
      const below = live.filter((level) => level.price < price).sort((a, b) => b.price - a.price)[0];
      if (above) {
        plans.push(card("sell", "SWP", `Sweep of ${above.name} ${round2(above.price)}`,
          `Wick above ${round2(above.price)}, the M5 candle closes back below it, then a bearish CHoCH within 1 hour → SELL on that close.`,
          [above.price, above.price + 0.5 * atr], above.price, above.price + 0.6 * atr,
          `Cancelled if an M5 candle closes above ${round2(above.price)} (level broken).`, price, true, "2R"));
      }
      if (below) {
        plans.push(card("buy", "SWP", `Sweep of ${below.name} ${round2(below.price)}`,
          `Wick below ${round2(below.price)}, the M5 candle closes back above it, then a bullish CHoCH within 1 hour → BUY on that close.`,
          [below.price - 0.5 * atr, below.price], below.price, below.price - 0.6 * atr,
          `Cancelled if an M5 candle closes below ${round2(below.price)} (level broken).`, price, true, "2R"));
      }
    }
  }
  const ordered = [...plans].sort((a, b) => Math.abs(a.distance) - Math.abs(b.distance));
  const first = (["buy", "sell"] as const)
    .map((side) => ordered.find((plan) => plan.side === side))
    .filter((plan): plan is PlanCard => plan != null);
  const rest = ordered.filter((plan) => !first.includes(plan));
  return { plans: [...first, ...rest].slice(0, 6).map((plan, index) => ({ ...plan, n: index + 1 })), notes };
}

export interface BookParts {
  bars: readonly LearnBar[];
  structure: Structure;
  lesson: Lesson;
  gaps: readonly StudyBox[];
  now: number;
  symbol: string;
}

export function bookFromParts(input: BookParts): SignalBook {
  const bars = input.bars.filter((bar) => closeOf(bar) <= input.now);
  if (bars.length < 2) {
    return { ...emptyBook(), notes: ["Not enough closed 5-minute candles yet."] };
  }
  const clock = istClock(input.now);
  if (clock.weekday === 0 || clock.weekday === 6) {
    return { ...emptyBook(), notes: ["Weekend — market closed."], price: bars[bars.length - 1]?.close ?? null };
  }
  const structure: Structure = {
    ...input.structure,
    events: input.structure.events
      .filter((event) => closeOf({ time: event.time, high: 0, low: 0, close: 0 }) <= input.now && event.index < bars.length)
      .sort((a, b) => a.time - b.time || a.index - b.index),
    swings: input.structure.swings.filter((swing) => swing.confirmIndex < bars.length),
  };
  const lesson: Lesson = {
    liquidity: input.lesson.liquidity,
    sweeps: input.lesson.sweeps.filter((sweep) => closeOf({ time: sweep.time, high: 0, low: 0, close: 0 }) <= input.now && sweep.index < bars.length),
    sessions: input.lesson.sessions,
  };
  const gaps = input.gaps.filter((gap) => gap.confirm + M5 <= input.now);
  const scan = scanIct(bars, structure.events, structure.swings, false);
  const swp = swpSignals(bars, structure, lesson, input.symbol);
  const fvg = fvgSignals(bars, structure, gaps, input.symbol, scan.overlaps);
  const amd = amdSignals(bars, structure, lesson, input.symbol);
  const models = scan.hits.flatMap((hit) => {
    const bar = bars[hit.index];
    if (!bar) return [];
    const signal = makeSignal(hit.setup, hit.side, input.symbol, bar, hit.slLevel, atrAtBar(bars, hit.index), null, "2R", hit.why);
    return signal ? [signal] : [];
  });
  const scored = attachContext(
    [...swp.signals, ...fvg.signals, ...amd, ...models]
      .map((signal) => scoreSignal(signal, bars, input.now))
      .sort((a, b) => a.time - b.time),
    bars,
    input.symbol,
  );
  const price = bars[bars.length - 1]!.close;
  const atr = atrAtBar(bars, bars.length - 1);
  const planned = buildPlans(bars, structure, lesson, gaps, scored, input.now, price, atr);
  return {
    plans: planned.plans,
    done: [...swp.done, ...fvg.done].filter((item) => item.time <= input.now).sort((a, b) => b.time - a.time).slice(0, 8),
    notes: planned.notes,
    signals: scored,
    stats: tally(scored),
    trend: trendAt(structure.events, bars[bars.length - 1]!.time),
    price,
    breakdown: whatWorks(scored),
  };
}

export function costInR(riskPoints: number, lot: number, rupees = OPTION_COST_INR): number {
  if (!(riskPoints > 0) || !(lot > 0)) return 0;
  return round2(rupees / (riskPoints * lot));
}

export function sessionSlot(epoch: number): SessionSlot {
  const minutes = istClock(epoch).minutes;
  if (minutes < 10 * 60 + 15) return "opening";
  if (minutes < 13 * 60 + 30) return "midday";
  return "closing";
}

export function levelGroup(why: string): LevelGroup {
  if (/\bPDH\b|\bPDL\b/.test(why)) return "PDH/PDL";
  if (/Week high|Week low/.test(why)) return "week";
  if (/OR high|OR low/.test(why)) return "OR";
  if (/\bEQH\b|\bEQL\b/.test(why)) return "EQH/EQL";
  return "other";
}

export function verdictOf(n: number, winRate: number | null, avgR: number | null): string {
  const edge = winRate != null && winRate >= 55 && (avgR ?? 0) > 0.1;
  const slight = winRate != null && winRate >= 50 && (avgR ?? 0) > 0;
  if (n < 50) {
    if (edge) return "edge, small sample";
    if (slight) return "slight edge, small sample";
    return "no edge, small sample";
  }
  if (edge) return "edge";
  if (slight) return "slight edge";
  return "no edge";
}

function bars15(bars: readonly LearnBar[]): LearnBar[] {
  const groups = new Map<number, LearnBar & { count: number }>();
  for (const bar of bars) {
    const clock = istClock(bar.time);
    if (clock.minutes < 9 * 60 + 15 || clock.minutes >= 15 * 60 + 30) continue;
    const offset = clock.minutes - (9 * 60 + 15);
    const start = bar.time - (offset % 15) * 60;
    const found = groups.get(start);
    if (!found) {
      groups.set(start, { time: start, open: bar.open ?? bar.close, high: bar.high, low: bar.low, close: bar.close, count: 1 });
    } else {
      found.high = Math.max(found.high, bar.high);
      found.low = Math.min(found.low, bar.low);
      found.close = bar.close;
      found.count += 1;
    }
  }
  return [...groups.values()].filter((bar) => bar.count >= 3).sort((a, b) => a.time - b.time);
}

function attachContext(signals: WatchSignal[], bars: readonly LearnBar[], symbol: string): WatchSignal[] {
  const higher = bars15(bars);
  const events = readStructure(higher, { left: 3, right: 3 }).events.map((event) => ({ ...event, time: event.time + 900 }));
  const lot = LOT[symbol] ?? 65;
  return signals.map((signal) => {
    const bias = trendAt(events, signal.time);
    const trend15: TrendAlign = bias == null ? "flat" : (bias === "up") === (signal.side === "buy") ? "with" : "against";
    return {
      ...signal,
      slot: sessionSlot(signal.time),
      trend15,
      level: levelGroup(signal.why),
      expiry: isExpiryDay(istClock(signal.time).day, symbol),
      costR: costInR(signal.risk, lot),
    };
  });
}

export function whatWorks(signals: readonly WatchSignal[]): BreakdownRow[] {
  const groups = new Map<string, WatchSignal[]>();
  for (const signal of signals) {
    const key = `${signal.setup}|${signal.slot}|${signal.trend15}|${signal.level}|${signal.expiry ? "expiry" : "other"}`;
    const rows = groups.get(key);
    if (rows) rows.push(signal);
    else groups.set(key, [signal]);
  }
  return [...groups.entries()].map(([, rows]) => {
    const stats = tally(rows).all;
    const net = rows.filter((row) => row.r != null);
    const avgRNet = net.length ? round2(net.reduce((sum, row) => sum + (row.r ?? 0) - row.costR, 0) / net.length) : null;
    return {
      setup: rows[0]!.setup,
      slot: rows[0]!.slot,
      trend15: rows[0]!.trend15,
      level: rows[0]!.level,
      expiry: rows[0]!.expiry,
      n: stats.signals,
      winRate: stats.winRate,
      avgR: stats.avgR,
      avgRNet,
      verdict: verdictOf(stats.signals, stats.winRate, stats.avgR),
    };
  }).sort((a, b) => b.n - a.n || a.setup.localeCompare(b.setup));
}

function freshGapState(bars: readonly LearnBar[], seeds: StudyBox[]): StudyBox[] {
  const indexOf = new Map<number, number>();
  bars.forEach((bar, index) => indexOf.set(bar.time, index));
  const pending = seeds.flatMap((gap) => {
    const at = indexOf.get(gap.confirm);
    return at == null ? [] : [{ ...gap, at }];
  });
  const active: { at: number; gap: StudyBox }[] = [];
  let next = 0;
  for (let i = 0; i < bars.length; i += 1) {
    while (next < pending.length && pending[next]!.at === i) {
      active.push({ at: pending[next]!.at, gap: pending[next]! });
      next += 1;
    }
    const bar = bars[i]!;
    for (let k = active.length - 1; k >= 0; k -= 1) {
      const item = active[k]!;
      if (item.at >= i) continue;
      if (i - item.at > FVG_BARS) {
        item.gap.state = "old";
        active.splice(k, 1);
        continue;
      }
      const up = item.gap.kind === "fvg_bull";
      const top = Math.max(item.gap.p1, item.gap.p2);
      const bottom = Math.min(item.gap.p1, item.gap.p2);
      if (up ? bar.close < bottom : bar.close > top) {
        item.gap.state = "filled";
        active.splice(k, 1);
      } else if (up ? bar.low <= top && item.gap.state === "open" : bar.high >= bottom && item.gap.state === "open") {
        item.gap.state = "tested";
      }
    }
  }
  return pending;
}

export function readSignalBook(
  bars: readonly LearnBar[],
  now: number,
  symbol: string,
  pivots?: { left?: number; right?: number },
): SignalBook {
  const closed = bars.filter((bar) => closeOf(bar) <= now);
  if (closed.length >= 3) {
    const step = closed[1]!.time - closed[0]!.time;
    if (step !== M5) return { ...emptyBook(), notes: ["Signals are read from closed 5-minute candles."] };
  }
  const structure = readStructure(closed, { left: pivots?.left ?? 3, right: pivots?.right ?? 3 });
  const levels = readLevels(closed);
  const lesson = readLesson({ bars: closed, structure, levels, timeframe: "5m", symbol });
  const gaps = freshGapState(closed, gapSeeds(closed));
  return bookFromParts({ bars: closed, structure, lesson, gaps, now, symbol });
}

/** Walk the cursor forward. The book at the last close matches one pass over the whole series. */
export function replayMatches(bars: readonly LearnBar[], symbol: string, pivots?: { left?: number; right?: number }): boolean {
  if (bars.length < 3) return true;
  const end = closeOf(bars[bars.length - 1]!);
  const full = readSignalBook(bars, end, symbol, pivots);
  let stepped = full;
  for (let i = 2; i < bars.length; i += 1) {
    stepped = readSignalBook(bars.slice(0, i + 1), closeOf(bars[i]!), symbol, pivots);
  }
  return JSON.stringify(stepped.signals) === JSON.stringify(full.signals);
}

export function expireStamp(day: string): number {
  const [year, month, date] = day.split("-").map(Number);
  return istEpoch(year ?? 1970, month ?? 1, date ?? 1, 15, 15);
}
