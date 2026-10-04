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
  /** Timeframe on screen when the drawing was created. Empty on older rows. */
  drawnOn: string;
  /** Timeframes this drawing is drawn on. Null means every timeframe. */
  showOn: readonly string[] | null;
  hidden: boolean;
  locked: boolean;
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

/** Hide text when the drawing is narrower than this on the current timeframe. */
export const NARROW_PX = 10;
/** Keep a Fibonacci label only when it sits at least this far from the previous one. */
export const LABEL_GAP_PX = 12;
/** A collapsed drawing is still selectable inside this radius. */
export const MIN_HIT_PX = 8;

const META_KEYS = new Set(["hidden", "locked", "showOn"]);

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

/** Null or a missing list means every timeframe. An empty list means none. */
export function visibleOnTimeframe(drawing: Drawing, timeframe: string): boolean {
  if (drawing.showOn == null) return true;
  return drawing.showOn.includes(timeframe);
}

export function shownDrawings(
  drawings: readonly Drawing[],
  cursor: number | null,
  hideAll: boolean,
  timeframe?: string,
): Drawing[] {
  if (hideAll) return [];
  return drawings.filter((item) => {
    if (item.hidden) return false;
    if (cursor != null && item.knownAt > cursor) return false;
    if (timeframe != null && !visibleOnTimeframe(item, timeframe)) return false;
    return true;
  });
}

export interface TreeRow {
  id: string;
  tool: DrawTool;
  drawnOn: string;
  createdAt: number;
  hidden: boolean;
  locked: boolean;
}

/** Every drawing for the symbol, including ones hidden on the current timeframe. */
export function objectTreeRows(drawings: readonly Drawing[]): TreeRow[] {
  return drawings.map((item) => ({
    id: item.id,
    tool: item.tool,
    drawnOn: item.drawnOn || "",
    createdAt: item.knownAt,
    hidden: item.hidden === true,
    locked: item.locked === true,
  }));
}

export interface PlacedLabel {
  y: number;
  text: string;
}

/** Under ~10px, draw lines only. A wider Fibonacci keeps the labels that do not overlap. */
export function labelsForWidth(
  tool: DrawTool,
  widthPx: number,
  labels: readonly PlacedLabel[],
  gapPx = LABEL_GAP_PX,
): PlacedLabel[] {
  if (widthPx < NARROW_PX) return [];
  if (tool !== "fib") return [...labels];
  const sorted = [...labels].sort((a, b) => a.y - b.y);
  const kept: PlacedLabel[] = [];
  for (const label of sorted) {
    const prev = kept[kept.length - 1];
    if (!prev || label.y - prev.y >= gapPx) kept.push(label);
  }
  return kept;
}

export interface Segment {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export function paddedSegment(seg: Segment, minPx = MIN_HIT_PX): Segment {
  const dx = seg.x2 - seg.x1;
  const dy = seg.y2 - seg.y1;
  const len = Math.hypot(dx, dy);
  if (len >= minPx) return seg;
  if (len === 0) return { x1: seg.x1 - minPx / 2, y1: seg.y1, x2: seg.x1 + minPx / 2, y2: seg.y1 };
  const scale = (minPx / len) / 2;
  const cx = (seg.x1 + seg.x2) / 2;
  const cy = (seg.y1 + seg.y2) / 2;
  return { x1: cx - dx * scale, y1: cy - dy * scale, x2: cx + dx * scale, y2: cy + dy * scale };
}

export function paddedBox(
  x: number,
  y: number,
  w: number,
  h: number,
  minPx = MIN_HIT_PX,
): { x: number; y: number; w: number; h: number } {
  const width = Math.max(w, minPx);
  const height = Math.max(h, minPx);
  return { x: x - (width - w) / 2, y: y - (height - h) / 2, w: width, h: height };
}

export function distanceToSegment(px: number, py: number, seg: Segment): number {
  const dx = seg.x2 - seg.x1;
  const dy = seg.y2 - seg.y1;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(px - seg.x1, py - seg.y1);
  const t = Math.max(0, Math.min(1, ((px - seg.x1) * dx + (py - seg.y1) * dy) / len2));
  return Math.hypot(px - (seg.x1 + t * dx), py - (seg.y1 + t * dy));
}

/** True when the point is within `radius` of the segment, after a collapsed segment is padded. */
export function hitsSegment(px: number, py: number, seg: Segment, radius = 6, minPx = MIN_HIT_PX): boolean {
  return distanceToSegment(px, py, paddedSegment(seg, minPx)) <= radius;
}

export function hitsBox(px: number, py: number, x: number, y: number, w: number, h: number, minPx = MIN_HIT_PX): boolean {
  const box = paddedBox(x, y, w, h, minPx);
  return px >= box.x && px <= box.x + box.w && py >= box.y && py <= box.y + box.h;
}

/** Logical range that puts both anchor times on screen, including two times that share one daily bar. */
export function zoomLogical(
  barTimes: readonly number[],
  fromTime: number,
  toTime: number,
  visible = 80,
): { from: number; to: number } | null {
  if (barTimes.length === 0) return null;
  const nearest = (target: number): number => {
    let best = 0;
    let bestDist = Infinity;
    for (let i = 0; i < barTimes.length; i += 1) {
      const dist = Math.abs((barTimes[i] ?? 0) - target);
      if (dist < bestDist) {
        best = i;
        bestDist = dist;
      }
    }
    return best;
  };
  const lo = Math.min(nearest(fromTime), nearest(toTime));
  const hi = Math.max(nearest(fromTime), nearest(toTime));
  const span = Math.max(visible, hi - lo + 20);
  let from = lo - Math.floor(span / 3);
  let to = from + span;
  if (from < 0) {
    to -= from;
    from = 0;
  }
  const lastTime = barTimes[barTimes.length - 1] ?? 0;
  const pastLast = fromTime > lastTime || toTime > lastTime;
  if (to > barTimes.length) {
    if (pastLast) {
      to = Math.max(barTimes.length + 8, hi + 8);
      from = Math.max(0, to - span);
    } else {
      from = Math.max(0, from - (to - barTimes.length));
      to = barTimes.length;
    }
  }
  return { from, to };
}

export function normalizeDrawing(raw: Drawing): Drawing {
  return {
    ...raw,
    drawnOn: typeof raw.drawnOn === "string" ? raw.drawnOn : "",
    showOn: Array.isArray(raw.showOn) ? raw.showOn : null,
    hidden: raw.hidden === true,
    locked: raw.locked === true,
  };
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

function isMetaPatch(patch: Partial<Drawing>): boolean {
  const keys = Object.keys(patch);
  return keys.length > 0 && keys.every((key) => META_KEYS.has(key));
}

export function editDrawing(doc: DrawDoc, id: string, patch: Partial<Drawing>): DrawDoc {
  const current = doc.drawings.find((item) => item.id === id);
  if (!current) return doc;
  const meta = isMetaPatch(patch);
  if ((doc.lockAll || current.locked) && !meta) return doc;
  return {
    ...doc,
    drawings: doc.drawings.map((item) => (item.id === id ? { ...item, ...patch } : item)),
  };
}

export function removeDrawing(doc: DrawDoc, id: string): DrawDoc {
  if (doc.lockAll) return doc;
  const current = doc.drawings.find((item) => item.id === id);
  if (!current || current.locked) return doc;
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
