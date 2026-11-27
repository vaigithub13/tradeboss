/**
 * Nifty study engines for the Learn chart.
 * Fair value gaps, order blocks and candle patterns follow the AurumBoss
 * definitions, measured in index points. AMD itself stays the Nifty opening
 * balance. Every mark is known only once its confirming candle has closed.
 */

import type { AmdSession, Lesson, Liquidity, Sweep } from "./lesson";
import type { BreakEvent, LearnBar, Structure, Swing } from "./structure";
import { istClock, istEpoch } from "./time";

export const FVG_MIN_ATR = 0.1;
export const PATTERN_NEAR_ATR = 0.3;
export const FVG_EVENT_ATR = 0.3;
const OPEN_FVG_KEEP = 8;
const OB_LOOKBACK = 30;
const OB_KEEP = 30;

export interface StudyBox {
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
  key?: boolean;
}

export interface StudyLine {
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
  short: boolean;
}

export interface StudyLabel {
  id: string;
  kind: string;
  time: number;
  price: number;
  text: string;
  color: string;
  textColor: string;
  pos: "above" | "below";
  detail: string;
  confirm: number;
  key?: boolean;
}

export interface StudySession {
  name: "Opening hour" | "Midday" | "Closing";
  t1: number;
  t2: number;
  color: string;
  text: string;
}

export interface StudyEvent {
  time: number;
  kind: string;
  text: string;
  detail: string;
  color: string;
  ref: { time: number; price: number };
}

export interface StudyNote {
  id: string;
  time: number;
  price: number;
  text: string;
  symbol: string;
}

export interface Study {
  sessions: StudySession[];
  boxes: StudyBox[];
  lines: StudyLine[];
  labels: StudyLabel[];
  events: StudyEvent[];
  banner: string;
  rounds: { price: number; major: boolean }[];
}

const GREEN = "#15803d";
const RED = "#b91c1c";
const AMBER = "#b45309";
const PURPLE = "#7e22ce";
const SLATE = "#475569";

export function daySessions(day: string): StudySession[] {
  const [year, month, date] = day.split("-").map(Number);
  const at = (hour: number, minute: number) => istEpoch(year ?? 1970, month ?? 1, date ?? 1, hour, minute);
  return [
    { name: "Opening hour", t1: at(9, 15), t2: at(10, 15), color: "rgba(250,204,21,0.13)", text: "#a16207" },
    { name: "Midday", t1: at(10, 15), t2: at(13, 30), color: "rgba(59,130,246,0.08)", text: "#1d4ed8" },
    { name: "Closing", t1: at(13, 30), t2: at(15, 30), color: "rgba(239,68,68,0.07)", text: "#b91c1c" },
  ];
}

export function sessionName(epoch: number): string {
  const clock = istClock(epoch);
  if (clock.weekday === 0 || clock.weekday === 6) return "Weekend";
  const hit = daySessions(clock.day).find((session) => epoch >= session.t1 && epoch < session.t2);
  if (hit) return hit.name;
  return clock.minutes < 9 * 60 + 15 ? "Before the open" : "After the close";
}

function atrSeries(bars: readonly LearnBar[]): number[] {
  const out: number[] = [];
  let sum = 0;
  for (let i = 0; i < bars.length; i += 1) {
    const bar = bars[i];
    const prev = i > 0 ? bars[i - 1]?.close ?? bar?.close ?? 0 : bar?.close ?? 0;
    const high = bar?.high ?? 0;
    const low = bar?.low ?? 0;
    const tr = i === 0 ? high - low : Math.max(high, prev) - Math.min(low, prev);
    sum += tr;
    out.push(sum / (i + 1));
  }
  return out;
}

function openOf(bar: LearnBar): number {
  return bar.open ?? bar.close;
}

function levelKind(name: string): string {
  if (name === "PDH") return "pdh";
  if (name === "PDL") return "pdl";
  if (name === "Week high") return "pwh";
  if (name === "Week low") return "pwl";
  if (name === "OR high") return "or_h";
  if (name === "OR low") return "or_l";
  if (name === "EQH") return "eqh";
  return "eql";
}

function timeAt(bars: readonly LearnBar[], index: number): number {
  if (bars.length === 0) return 0;
  const i = Math.min(Math.max(0, index), bars.length - 1);
  return bars[i]!.time;
}

function lineOf(level: Liquidity, bars: readonly LearnBar[], end: number): StudyLine {
  const known = timeAt(bars, level.knownAt);
  const kind = levelKind(level.name);
  return {
    id: `liq-${level.name}-${level.price}`,
    kind,
    t1: known,
    t2: end,
    price: level.price,
    color: level.name.startsWith("EQ") ? "#7c3aed" : "#64748b",
    dashed: true,
    label: level.tag,
    detail: `${level.name} at ${round(level.price)}`,
    confirm: known,
    short: false,
  };
}

function round(price: number): string {
  return Number.isInteger(price) ? String(price) : price.toFixed(2);
}

/** Gap prices only. The fill is decided later, one bar at a time, so a long history stays causal. */
export function gapSeeds(bars: readonly LearnBar[]): StudyBox[] {
  const atr = atrSeries(bars);
  const out: StudyBox[] = [];
  for (let j = 2; j < bars.length; j += 1) {
    const left = bars[j - 2];
    const right = bars[j];
    const unit = atr[j] ?? 0;
    if (!left || !right || unit <= 0) continue;
    const up = right.low - left.high;
    const down = left.low - right.high;
    let top = 0;
    let bottom = 0;
    let bull = false;
    if (up >= FVG_MIN_ATR * unit) {
      bull = true;
      top = right.low;
      bottom = left.high;
    } else if (down >= FVG_MIN_ATR * unit) {
      top = left.low;
      bottom = right.high;
    } else continue;
    const size = top - bottom;
    out.push({
      id: `fvg-${right.time}-${bull ? "up" : "down"}`,
      kind: bull ? "fvg_bull" : "fvg_bear",
      t1: left.time,
      t2: right.time,
      p1: top,
      p2: bottom,
      color: bull ? "rgba(22,163,74,0.16)" : "rgba(220,38,38,0.14)",
      border: bull ? "rgba(22,163,74,0.55)" : "rgba(220,38,38,0.5)",
      label: "",
      labelColor: bull ? GREEN : RED,
      detail: `${bull ? "Bullish" : "Bearish"} gap ${round(bottom)}–${round(top)} (${round(size)} pts), open`,
      confirm: right.time,
      state: "open",
    });
  }
  return out;
}

export function fairValueGaps(bars: readonly LearnBar[]): StudyBox[] {
  const atr = atrSeries(bars);
  const out: StudyBox[] = [];
  for (let j = 2; j < bars.length; j += 1) {
    const left = bars[j - 2];
    const right = bars[j];
    const unit = atr[j] ?? 0;
    if (!left || !right || unit <= 0) continue;
    const up = right.low - left.high;
    const down = left.low - right.high;
    let top = 0;
    let bottom = 0;
    let bull = false;
    if (up >= FVG_MIN_ATR * unit) {
      bull = true;
      top = right.low;
      bottom = left.high;
    } else if (down >= FVG_MIN_ATR * unit) {
      top = left.low;
      bottom = right.high;
    } else {
      continue;
    }
    let state = "open";
    let end = bars[bars.length - 1]?.time ?? right.time;
    for (let k = j + 1; k < bars.length; k += 1) {
      const bar = bars[k];
      if (!bar) continue;
      if (bull ? bar.low <= bottom : bar.high >= top) {
        state = "filled";
        end = bar.time;
        break;
      }
      if (bull ? bar.low <= top : bar.high >= bottom) state = "tested";
    }
    const size = top - bottom;
    out.push({
      id: `fvg-${right.time}-${bull ? "up" : "down"}`,
      kind: bull ? "fvg_bull" : "fvg_bear",
      t1: left.time,
      t2: end,
      p1: top,
      p2: bottom,
      color: bull ? "rgba(22,163,74,0.16)" : "rgba(220,38,38,0.14)",
      border: bull ? "rgba(22,163,74,0.55)" : "rgba(220,38,38,0.5)",
      label: "",
      labelColor: bull ? GREEN : RED,
      detail: `${bull ? "Bullish" : "Bearish"} gap ${round(bottom)}–${round(top)} (${round(size)} pts), ${state}`,
      confirm: right.time,
      state: size >= FVG_EVENT_ATR * unit ? state : `${state}-small`,
    });
  }
  const open = out.filter((box) => box.state === "open" || box.state === "tested");
  if (open.length > OPEN_FVG_KEEP) {
    for (const box of open.slice(0, open.length - OPEN_FVG_KEEP)) box.state = "old";
  }
  return out;
}

export function orderBlocks(bars: readonly LearnBar[], events: readonly BreakEvent[]): StudyBox[] {
  const out: StudyBox[] = [];
  for (const event of events) {
    const up = event.direction === "up";
    for (let q = event.index - 1; q >= Math.max(0, event.index - OB_LOOKBACK); q -= 1) {
      const candle = bars[q];
      if (!candle) continue;
      const bodyDown = candle.close < openOf(candle);
      const bodyUp = candle.close > openOf(candle);
      if ((up && !bodyDown) || (!up && !bodyUp)) continue;
      let broken = false;
      let end = bars[bars.length - 1]?.time ?? candle.time;
      for (let k = event.index; k < bars.length; k += 1) {
        const later = bars[k];
        if (!later) continue;
        if ((up && later.close < candle.low) || (!up && later.close > candle.high)) {
          broken = true;
          end = later.time;
          break;
        }
      }
      out.push({
        id: `ob-${event.time}-${up ? "up" : "down"}`,
        kind: up ? "ob_bull" : "ob_bear",
        t1: candle.time,
        t2: end,
        p1: candle.high,
        p2: candle.low,
        color: up ? "rgba(22,163,74,0.12)" : "rgba(220,38,38,0.12)",
        border: up ? "rgba(22,163,74,0.7)" : "rgba(220,38,38,0.7)",
        label: up ? "OB" : "OB",
        labelColor: up ? GREEN : RED,
        detail: `${up ? "Bullish" : "Bearish"} order block ${round(candle.low)}–${round(candle.high)}${broken ? ", broken" : ""}`,
        confirm: event.time,
        state: broken ? "broken" : "open",
      });
      break;
    }
  }
  return out.slice(-OB_KEEP);
}

interface KeyLevel {
  price: number;
  side: "high" | "low";
  name: string;
}

function patternKeys(
  bar: LearnBar,
  bull: boolean,
  levels: readonly KeyLevel[],
  gaps: readonly StudyBox[],
  unit: number,
): string | null {
  const near = PATTERN_NEAR_ATR * unit;
  for (const level of levels) {
    if (bull && level.side === "low" && Math.abs(bar.low - level.price) <= near) return level.name;
    if (!bull && level.side === "high" && Math.abs(bar.high - level.price) <= near) return level.name;
  }
  for (const gap of gaps) {
    if (gap.confirm > bar.time) continue;
    const same = bull ? gap.kind === "fvg_bull" : gap.kind === "fvg_bear";
    if (!same) continue;
    const top = Math.max(gap.p1, gap.p2);
    const bottom = Math.min(gap.p1, gap.p2);
    const edge = bull ? bar.low : bar.high;
    if (edge <= top + near && edge >= bottom - near) return "a fair value gap";
  }
  return null;
}

export function candlePatterns(
  bars: readonly LearnBar[],
  levels: readonly KeyLevel[],
  gaps: readonly StudyBox[],
): StudyLabel[] {
  const atr = atrSeries(bars);
  const out: StudyLabel[] = [];
  for (let i = 1; i < bars.length; i += 1) {
    const bar = bars[i];
    const prev = bars[i - 1];
    const unit = atr[i] ?? 0;
    if (!bar || !prev || unit <= 0) continue;
    const open = openOf(bar);
    const prevOpen = openOf(prev);
    const body = Math.abs(bar.close - open);
    const prevBody = Math.abs(prev.close - prevOpen);
    const range = Math.max(bar.high - bar.low, 1e-9);
    const upper = bar.high - Math.max(open, bar.close);
    const lower = Math.min(open, bar.close) - bar.low;
    let kind = "";
    let text = "";
    let bull = false;
    if (prev.close < prevOpen && bar.close > open && open <= prev.close && bar.close >= prevOpen && body > prevBody) {
      kind = "engulf_bull";
      text = "Engulf";
      bull = true;
    } else if (prev.close > prevOpen && bar.close < open && open >= prev.close && bar.close <= prevOpen && body > prevBody) {
      kind = "engulf_bear";
      text = "Engulf";
    } else if (lower >= 2 * body && lower >= 0.6 * range && upper <= 0.25 * range) {
      kind = "pin_bull";
      text = "Pin";
      bull = true;
    } else if (upper >= 2 * body && upper >= 0.6 * range && lower <= 0.25 * range) {
      kind = "pin_bear";
      text = "Pin";
    } else if (body <= 0.1 * range) {
      kind = "doji";
      text = "Doji";
    } else if (bar.high < prev.high && bar.low > prev.low) {
      kind = "inside";
      text = "Inside";
    } else {
      continue;
    }
    const key = kind === "doji" || kind === "inside" ? null : patternKeys(bar, bull, levels, gaps, unit);
    out.push({
      id: `pat-${bar.time}-${kind}`,
      kind,
      time: bar.time,
      price: bull || kind === "doji" || kind === "inside" ? bar.low : bar.high,
      text,
      color: bull ? GREEN : kind.endsWith("bear") ? RED : SLATE,
      textColor: "#ffffff",
      pos: bull || kind === "doji" || kind === "inside" ? "below" : "above",
      detail: key ? `${text} at ${key}` : text,
      confirm: bar.time,
      key: key != null,
    });
  }
  return out;
}

function swingLabel(swing: Swing, bars: readonly LearnBar[]): StudyLabel {
  const high = swing.kind === "high";
  const kind = swing.label === "H" ? "swing_high" : swing.label === "L" ? "swing_low" : swing.label.toLowerCase();
  const up = kind === "hh" || kind === "hl" || kind === "swing_low";
  return {
    id: `swing-${swing.time}-${swing.label}`,
    kind,
    time: swing.time,
    price: swing.price,
    text: swing.label,
    color: up ? GREEN : RED,
    textColor: "#ffffff",
    pos: high ? "above" : "below",
    detail: `${swing.label} at ${round(swing.price)}`,
    confirm: bars[swing.confirmIndex]?.time ?? swing.time,
  };
}

function breakLine(event: BreakEvent): StudyLine {
  return {
    id: `brk-${event.time}-${event.kind}`,
    kind: event.kind,
    t1: event.time,
    t2: event.time,
    price: event.price,
    color: event.kind === "bos" ? "#2563eb" : PURPLE,
    dashed: false,
    label: event.kind === "bos" ? "BOS" : "CHoCH",
    detail: `${event.kind === "bos" ? "Break of structure" : "Change of character"} ${event.direction} at ${round(event.price)}`,
    confirm: event.time,
    short: true,
  };
}

function sweepLabel(sweep: Sweep): StudyLabel {
  return {
    id: `sweep-${sweep.time}-${sweep.name}`,
    kind: "sweep",
    time: sweep.time,
    price: sweep.extreme,
    text: "Sweep",
    color: AMBER,
    textColor: "#111827",
    pos: sweep.side === "high" ? "above" : "below",
    detail: `Sweep of ${sweep.name} at ${round(sweep.price)}, extreme ${round(sweep.extreme)}`,
    confirm: sweep.time,
  };
}

function amdBoxes(session: AmdSession, end: number): StudyBox[] {
  const boxes: StudyBox[] = [];
  if (session.accumulation) {
    boxes.push({
      id: `amd-a-${session.day}`,
      kind: "amd_a",
      t1: session.accumulation.from,
      t2: session.accumulation.to,
      p1: session.accumulation.high,
      p2: session.accumulation.low,
      color: "rgba(120,128,140,0.16)",
      border: "rgba(71,85,105,0.4)",
      label: "A",
      labelColor: SLATE,
      detail: `Opening balance ${round(session.accumulation.low)}–${round(session.accumulation.high)}`,
      confirm: session.accumulation.to,
    });
  }
  if (session.distribution) {
    const up = session.distribution.direction === "up";
    boxes.push({
      id: `amd-d-${session.day}`,
      kind: "amd_d",
      t1: session.distribution.from,
      t2: session.distribution.to,
      p1: session.accumulation?.high ?? session.distribution.from,
      p2: session.accumulation?.low ?? session.distribution.from,
      color: up ? "rgba(22,163,74,0.10)" : "rgba(220,38,38,0.10)",
      border: "transparent",
      label: up ? "D ↑" : "D ↓",
      labelColor: up ? GREEN : RED,
      detail: `Distribution ${session.distribution.direction}`,
      confirm: session.distribution.from,
    });
  }
  for (const box of boxes) {
    if (box.t2 < box.t1) box.t2 = end;
  }
  return boxes;
}

function manipLabel(session: AmdSession): StudyLabel | null {
  const manip = session.manipulation;
  if (!manip) return null;
  return {
    id: `amd-m-${session.day}`,
    kind: "amd_m",
    time: manip.time,
    price: manip.extreme,
    text: "M",
    color: PURPLE,
    textColor: "#ffffff",
    pos: manip.side === "high" ? "above" : "below",
    detail: `Manipulation: sweep of ${manip.name}`,
    confirm: manip.time,
  };
}

/** A later close back through the swept level means the raid did not hold. */
export function sweepFailed(session: AmdSession, bars: readonly LearnBar[]): boolean {
  const manip = session.manipulation;
  if (!manip || session.distribution) return false;
  for (let i = manip.index + 1; i < bars.length; i += 1) {
    const bar = bars[i]!;
    if (manip.side === "low" && bar.low < manip.extreme && bar.close < manip.price) return true;
    if (manip.side === "high" && bar.high > manip.extreme && bar.close > manip.price) return true;
  }
  return false;
}

export function bannerText(session: AmdSession | null, now: number, failed: boolean): string {
  const name = sessionName(now);
  if (name === "Weekend") return "Weekend — market closed. Use Replay to practise.";
  if (name === "Before the open") return "Before the open — waiting for 09:15.";
  if (!session) return `${name} — not enough of the session yet.`;
  if (session.note === "No AMD today") return "No AMD today. Stay patient.";
  if (failed && session.manipulation) {
    return `Sweep of ${session.manipulation.name} failed — no AMD read; stay patient`;
  }
  if (session.distribution) {
    const way = session.distribution.direction === "up" ? "up" : "down";
    return `Distribution ${way} — closed beyond the other side of the opening balance.`;
  }
  if (session.manipulation) {
    return `Manipulation — sweep of ${session.manipulation.name} closed back inside. Watching for distribution.`;
  }
  if (!session.accumulation) {
    return name === "Opening hour" ? "Opening hour — accumulation forming" : `${name} — opening balance still forming.`;
  }
  return `${name} — opening balance is set. Watching for a sweep.`;
}

function keyLevels(lesson: Lesson): KeyLevel[] {
  return lesson.liquidity.map((level) => ({ price: level.price, side: level.side, name: level.name }));
}

export function roundMarks(low: number, high: number, last: number): { price: number; major: boolean }[] {
  const span = Math.max(high - low, 100);
  const from = Math.floor((last - span) / 50) * 50;
  const to = Math.ceil((last + span) / 50) * 50;
  const marks: { price: number; major: boolean }[] = [];
  for (let price = from; price <= to && marks.length < 40; price += 50) {
    marks.push({ price, major: price % 100 === 0 });
  }
  return marks;
}

export interface StudyLayers {
  session: boolean;
  amd: boolean;
  swing: boolean;
  structure: boolean;
  liquidity: boolean;
  fvg: boolean;
  fvgFilled: boolean;
  ob: boolean;
  pattern: boolean;
  patternAll: boolean;
  round: boolean;
  notes: boolean;
  signals: boolean;
}

export const DEFAULT_LAYERS: StudyLayers = {
  session: true,
  amd: true,
  swing: true,
  structure: true,
  liquidity: true,
  fvg: true,
  fvgFilled: false,
  ob: false,
  pattern: true,
  patternAll: false,
  round: true,
  notes: true,
  signals: true,
};

export function recentLabels(labels: readonly StudyLabel[], limit: number): StudyLabel[] {
  return [...labels].sort((a, b) => a.confirm - b.confirm || a.time - b.time).slice(-Math.max(0, limit));
}

export interface StudyInput {
  bars: readonly LearnBar[];
  structure: Structure;
  lesson: Lesson;
  now: number;
  low: number;
  high: number;
  last: number;
}

export function buildStudy(input: StudyInput): Study {
  const { bars, structure, lesson, now } = input;
  const end = bars[bars.length - 1]?.time ?? now;
  const days = [...new Set(bars.map((bar) => istClock(bar.time).day))];
  if (now) days.push(istClock(now).day);
  const sessions = [...new Set(days)].flatMap((day) => daySessions(day));
  const gaps = fairValueGaps(bars);
  const blocks = orderBlocks(bars, structure.events);
  const labels: StudyLabel[] = [
    ...structure.swings.map((swing) => swingLabel(swing, bars)),
    ...lesson.sweeps.map(sweepLabel),
    ...candlePatterns(bars, keyLevels(lesson), gaps),
  ];
  const lines: StudyLine[] = [
    ...lesson.liquidity.map((level) => lineOf(level, bars, end)),
    ...structure.events.map(breakLine),
  ];
  const boxes: StudyBox[] = [...gaps, ...blocks];
  for (const session of lesson.sessions) {
    boxes.push(...amdBoxes(session, end));
    const mark = manipLabel(session);
    if (mark) labels.push(mark);
  }
  const latest = lesson.sessions.at(-1) ?? null;
  const failed = latest ? sweepFailed(latest, bars) : false;
  const events = collectEvents(bars, structure, lesson, gaps, labels, latest, failed);
  events.sort((a, b) => b.time - a.time || a.text.localeCompare(b.text));
  return {
    sessions,
    boxes,
    lines,
    labels,
    events: events.slice(0, 300),
    banner: bannerText(latest, now || end, failed),
    rounds: roundMarks(input.low, input.high, input.last || bars[bars.length - 1]?.close || 0),
  };
}

function collectEvents(
  bars: readonly LearnBar[],
  structure: Structure,
  lesson: Lesson,
  gaps: readonly StudyBox[],
  labels: readonly StudyLabel[],
  latest: AmdSession | null,
  failed: boolean,
): StudyEvent[] {
  const events: StudyEvent[] = [];
  for (const event of structure.events) {
    events.push({
      time: event.time,
      kind: event.kind,
      text: event.kind === "bos" ? `BOS ${event.direction}` : `CHoCH ${event.direction}`,
      detail: `${event.kind} at ${round(event.price)}`,
      color: event.kind === "bos" ? "#2563eb" : PURPLE,
      ref: { time: event.time, price: event.price },
    });
  }
  for (const sweep of lesson.sweeps) {
    events.push({
      time: sweep.time,
      kind: "sweep",
      text: `Sweep of ${sweep.name}`,
      detail: `Closed back after trading through ${round(sweep.price)}`,
      color: AMBER,
      ref: { time: sweep.time, price: sweep.extreme },
    });
  }
  for (const gap of gaps) {
    if (gap.state === "open-small" || gap.state === "tested-small" || gap.state === "filled-small") continue;
    events.push({
      time: gap.confirm,
      kind: gap.kind,
      text: gap.kind === "fvg_bull" ? "Bullish gap formed" : "Bearish gap formed",
      detail: gap.detail,
      color: gap.kind === "fvg_bull" ? GREEN : RED,
      ref: { time: gap.t1, price: (gap.p1 + gap.p2) / 2 },
    });
    if (gap.state === "filled") {
      events.push({
        time: gap.t2,
        kind: gap.kind,
        text: gap.kind === "fvg_bull" ? "Bullish gap filled" : "Bearish gap filled",
        detail: gap.detail,
        color: gap.labelColor,
        ref: { time: gap.t2, price: (gap.p1 + gap.p2) / 2 },
      });
    }
  }
  for (const label of labels) {
    if (!label.key) continue;
    events.push({
      time: label.time,
      kind: label.kind,
      text: label.detail,
      detail: label.detail,
      color: label.color,
      ref: { time: label.time, price: label.price },
    });
  }
  for (const session of lesson.sessions) {
    if (session.accumulation) {
      events.push({
        time: session.accumulation.to,
        kind: "amd_a",
        text: `Opening balance done: ${round(session.accumulation.low)}–${round(session.accumulation.high)}`,
        detail: `Opening balance ${round(session.accumulation.low)}–${round(session.accumulation.high)}`,
        color: SLATE,
        ref: { time: session.accumulation.to, price: session.accumulation.high },
      });
    }
    if (session.manipulation) {
      events.push({
        time: session.manipulation.time,
        kind: "amd_m",
        text: `Manipulation — sweep of ${session.manipulation.name}`,
        detail: `Sweep of ${session.manipulation.name}`,
        color: PURPLE,
        ref: { time: session.manipulation.time, price: session.manipulation.extreme },
      });
    }
    if (session.distribution) {
      events.push({
        time: session.distribution.from,
        kind: "amd_d",
        text: `Distribution ${session.distribution.direction}`,
        detail: `Distribution ${session.distribution.direction}`,
        color: session.distribution.direction === "up" ? GREEN : RED,
        ref: { time: session.distribution.from, price: session.accumulation?.high ?? 0 },
      });
    }
    if (session.note) {
      const dayBars = bars.filter((item) => istClock(item.time).day === session.day);
      const bar = dayBars.find((item) => istClock(item.time).minutes >= 15 * 60 + 15) ?? dayBars.at(-1);
      events.push({
        time: bar?.time ?? 0,
        kind: "amd_a",
        text: "No AMD today",
        detail: "The session finished without a completed AMD read.",
        color: SLATE,
        ref: { time: bar?.time ?? 0, price: bar?.close ?? 0 },
      });
    }
  }
  if (failed && latest?.manipulation) {
    const later = bars.find((bar, index) => index > latest.manipulation!.index && (
      (latest.manipulation!.side === "low" && bar.close < latest.manipulation!.price)
      || (latest.manipulation!.side === "high" && bar.close > latest.manipulation!.price)
    ));
    if (later) {
      events.push({
        time: later.time,
        kind: "sweep",
        text: `Sweep of ${latest.manipulation.name} failed`,
        detail: "Price went back through the swept level.",
        color: RED,
        ref: { time: later.time, price: later.close },
      });
    }
  }
  return events;
}
