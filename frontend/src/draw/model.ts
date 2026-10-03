/**
 * Drawing geometry that does not touch the chart.
 * Anchors stay { time, price }. The screen position is computed when drawing.
 */
import type { Timeframe } from "../api/client";

export interface Anchor {
  time: number;
  price: number;
}

export interface BarPrices {
  open: number;
  high: number;
  low: number;
  close: number;
}

export type DrawTool =
  | "trend"
  | "ray"
  | "extended"
  | "horizontal"
  | "horizontal_ray"
  | "vertical"
  | "rectangle"
  | "fib"
  | "text"
  | "measure";

export type LineStyleName = "solid" | "dashed" | "dotted";

export interface DrawStyle {
  color: string;
  width: number;
  lineStyle: LineStyleName;
  extendLeft: boolean;
  extendRight: boolean;
  fill: string | null;
}

export interface Drawing {
  id: string;
  tool: DrawTool;
  anchors: Anchor[];
  /** Replay cursor when the drawing was made, or the last bar's time when it was made live. */
  knownAt: number;
  text: string;
  style: DrawStyle;
}

export interface DrawDoc {
  symbol: string;
  drawings: Drawing[];
  lockAll: boolean;
  hideAll: boolean;
}

export interface MeasureResult {
  price: number;
  percent: number;
  seconds: number;
  bars: number;
}

export interface FibLevel {
  ratio: number;
  price: number;
}

const IST = 19_800;
const DAY = 86_400;
const OPEN_MIN = 9 * 60 + 15;
const CLOSE_MIN = 15 * 60 + 30;

const INTRADAY: Record<string, number> = { "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60 };

export const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1] as const;

export const TWO_ANCHOR: ReadonlySet<DrawTool> = new Set([
  "trend",
  "ray",
  "extended",
  "rectangle",
  "fib",
  "measure",
]);

export function defaultStyle(tool: DrawTool): DrawStyle {
  const both = tool === "extended" || tool === "horizontal";
  const right = both || tool === "ray" || tool === "horizontal_ray";
  return {
    color: "#2962ff",
    width: 1,
    lineStyle: "solid",
    extendLeft: both,
    extendRight: right,
    fill: tool === "rectangle" ? "#2962ff" : null,
  };
}

/** Unix seconds of the bar that contains `time` (09:15 IST buckets, daily 09:15, weekly Monday 09:15). */
export function barStart(time: number, timeframe: string): number {
  const day = Math.floor((time + IST) / DAY);
  const minute = Math.floor(((time + IST) % DAY) / 60);
  if (timeframe === "1D") return day * DAY - IST + OPEN_MIN * 60;
  if (timeframe === "1W") {
    const monday = day - ((day + 3) % 7);
    return monday * DAY - IST + OPEN_MIN * 60;
  }
  const step = INTRADAY[timeframe] ?? 15;
  const clamped = Math.min(Math.max(minute, OPEN_MIN), CLOSE_MIN - step);
  const k = Math.floor((clamped - OPEN_MIN) / step);
  return day * DAY - IST + (OPEN_MIN + k * step) * 60;
}

export function mapAnchor(anchor: Anchor, timeframe: string): Anchor {
  return { time: barStart(anchor.time, timeframe), price: anchor.price };
}

function istDate(day: number): string {
  const noon = new Date((day * DAY - IST + 12 * 3600) * 1000);
  const m = String(noon.getUTCMonth() + 1).padStart(2, "0");
  const d = String(noon.getUTCDate()).padStart(2, "0");
  return `${noon.getUTCFullYear()}-${m}-${d}`;
}

function isWeekend(day: number): boolean {
  return (day + 3) % 7 >= 5;
}

/** Future session bar starts after `lastBarTime`. Nights, weekends, and holiday dates are skipped. */
export function whitespaceTimes(
  lastBarTime: number,
  timeframe: string,
  count: number,
  holidays: readonly string[] = [],
): number[] {
  const closed = new Set(holidays);
  const step = INTRADAY[timeframe];
  const out: number[] = [];
  let day = Math.floor((lastBarTime + IST) / DAY);
  const guard = day + 400;
  if (timeframe === "1D" || timeframe === "1W" || step === undefined) {
    day += 1;
    while (out.length < count && day < guard) {
      const monday = (day + 3) % 7 === 0;
      const open = timeframe === "1W" ? monday && !closed.has(istDate(day)) : !isWeekend(day) && !closed.has(istDate(day));
      if (open) out.push(day * DAY - IST + OPEN_MIN * 60);
      day += 1;
    }
    return out;
  }
  while (out.length < count && day < guard) {
    if (!isWeekend(day) && !closed.has(istDate(day))) {
      for (let minute = OPEN_MIN; minute < CLOSE_MIN; minute += step) {
        const slot = day * DAY - IST + minute * 60;
        if (slot > lastBarTime) out.push(slot);
        if (out.length >= count) break;
      }
    }
    day += 1;
  }
  return out;
}

/** Nearest of open, high, low, close. An equal distance keeps the earlier one. */
export function snapPrice(price: number, bar: BarPrices): number {
  const options = [bar.open, bar.high, bar.low, bar.close];
  let best = options[0] ?? price;
  let bestDist = Math.abs(price - best);
  for (const value of options.slice(1)) {
    const dist = Math.abs(price - value);
    if (dist < bestDist) {
      best = value;
      bestDist = dist;
    }
  }
  return best;
}

export function shownDrawings(drawings: readonly Drawing[], cursor: number | null, hideAll: boolean): Drawing[] {
  if (hideAll) return [];
  if (cursor == null) return [...drawings];
  return drawings.filter((item) => item.knownAt <= cursor);
}

/** Replay stamps the cursor. A live drawing stamps the last bar, so a later live drawing stays hidden in replay. */
export function stampKnownAt<T extends Drawing>(drawing: T, cursor: number | null, lastBarTime: number): T {
  return { ...drawing, knownAt: cursor ?? lastBarTime };
}

export interface History {
  commit(doc: DrawDoc): void;
  undo(): DrawDoc;
  redo(): DrawDoc;
}

export function createHistory(initial: DrawDoc): History {
  const stack = [initial];
  let index = 0;
  return {
    commit(doc) {
      stack.splice(index + 1);
      stack.push(doc);
      index = stack.length - 1;
    },
    undo() {
      if (index > 0) index -= 1;
      return stack[index] ?? initial;
    },
    redo() {
      if (index < stack.length - 1) index += 1;
      return stack[index] ?? initial;
    },
  };
}

export function editDrawing(doc: DrawDoc, id: string, patch: Partial<Drawing>): DrawDoc {
  if (doc.lockAll) return doc;
  return {
    ...doc,
    drawings: doc.drawings.map((item) => (item.id === id ? { ...item, ...patch } : item)),
  };
}

export function removeDrawing(doc: DrawDoc, id: string): DrawDoc {
  if (doc.lockAll) return doc;
  return { ...doc, drawings: doc.drawings.filter((item) => item.id !== id) };
}

export function translate(drawing: Drawing, seconds: number, price: number): Drawing {
  return {
    ...drawing,
    anchors: drawing.anchors.map((anchor) => ({ time: anchor.time + seconds, price: anchor.price + price })),
  };
}

export function setAnchor(drawing: Drawing, index: number, anchor: Anchor): Drawing {
  return {
    ...drawing,
    anchors: drawing.anchors.map((item, i) => (i === index ? { time: anchor.time, price: anchor.price } : item)),
  };
}

export function fibPrices(a: Anchor, b: Anchor): FibLevel[] {
  return FIB_RATIOS.map((ratio) => ({ ratio, price: b.price + (a.price - b.price) * ratio }));
}

export function measure(a: Anchor, b: Anchor, timeframe: string): MeasureResult {
  const price = b.price - a.price;
  const percent = a.price === 0 ? 0 : (price / a.price) * 100;
  const seconds = b.time - a.time;
  const step = (INTRADAY[timeframe] ?? (timeframe === "1D" ? 1440 : 10080)) * 60;
  const bars = Math.round((barStart(b.time, timeframe) - barStart(a.time, timeframe)) / step);
  return { price, percent, seconds, bars };
}

export function barSeconds(timeframe: Timeframe | string): number {
  const minutes = INTRADAY[timeframe];
  if (minutes) return minutes * 60;
  if (timeframe === "1D") return (CLOSE_MIN - OPEN_MIN) * 60;
  return 4 * DAY + (CLOSE_MIN - OPEN_MIN) * 60;
}
