/**
 * First Hour Box (Strategy C chart overlay).
 *
 * Candles A=09:15 and B=09:45 (30m, IST). Box high/low/mid/width from those.
 * 09:15–10:15: forming (live). At 10:15 lock; status on 30m closes 10:45…15:15.
 */

export interface FhbCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export type FhbSide = "above" | "below";
export type FhbKind = "PROBE" | "BREAK" | "ACCEPTED" | "FAILED" | "PROBE_BOTH" | "INSIDE";

export interface FhbMarker {
  time: number;
  kind: FhbKind;
  side: FhbSide | null;
  label: string;
  color: string;
}

export interface FhbDayBox {
  day: string;
  /** Box high = max(high A, high B) */
  high: number;
  /** Box low = min(low A, low B) */
  low: number;
  mid: number;
  width: number;
  tooWide: boolean;
  /** 09:15 unix */
  startTime: number;
  /** 10:15 unix — lock / ready */
  readyTime: number;
  /** 15:30 unix of that IST day */
  sessionEndTime: number;
  /** shade end = readyTime (locked box covers 09:15–10:15 only) */
  shadeEndTime: number;
  forming: boolean;
  markers: FhbMarker[];
  /** latest status after the last closed 30m, or INSIDE / FORMING */
  lastStatus: FhbKind | "FORMING";
  lastStatusTime: number | null;
  lastStatusLabel: string;
}

export interface FirstHourBoxResult {
  boxes: FhbDayBox[];
  markers: FhbMarker[];
  /** next 30m bar close (unix) after asOf, or null */
  nextCloseTime: number | null;
}

export const DEFAULT_MAX_WIDTH = 150;

const IST = 19_800;
const DAY = 86_400;
const OPEN_MIN = 9 * 60 + 15;
const READY_MIN = 10 * 60 + 15;
const CLOSE_MIN = 15 * 60 + 30;
const LAST_STATUS_MIN = 14 * 60 + 45;
const STUB_MIN = 15 * 60 + 15;

/** Spec colours (overridable from indicator colours in the UI). */
export const FHB_COLORS = {
  probe: "#f59e0b",
  break: "#3b82f6",
  acceptedAbove: "#16a34a",
  acceptedBelow: "#dc2626",
  failed: "#a855f7",
  inside: "#94a3b8",
  high: "#16a34a",
  low: "#dc2626",
  mid: "#94a3b8",
  box: "#2962ff",
} as const;

export function formatInrPrice(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Axis titles: "BOX H 24,350" (no decimals when whole). */
export function formatBoxAxisLabel(prefix: string, n: number): string {
  if (!Number.isFinite(n)) return prefix;
  const rounded = Math.round(n * 100) / 100;
  const text =
    Number.isInteger(rounded) || Math.abs(rounded - Math.round(rounded)) < 1e-9
      ? Math.round(rounded).toLocaleString("en-IN")
      : formatInrPrice(rounded);
  return `${prefix} ${text}`;
}

export function istDayKey(unix: number): string {
  const day = Math.floor((unix + IST) / DAY);
  const utc = new Date((day * DAY - IST) * 1000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(utc);
}

export function istMinuteOfDay(unix: number): number {
  return Math.floor(((unix + IST) % DAY) / 60);
}

export function istHhmm(unix: number): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(unix * 1000));
}

export function thirtyOpenUnix(unix: number): number | null {
  const day = Math.floor((unix + IST) / DAY);
  const minute = istMinuteOfDay(unix);
  if (minute < OPEN_MIN || minute >= CLOSE_MIN) return null;
  if (minute >= STUB_MIN) return null;
  const k = Math.floor((minute - OPEN_MIN) / 30);
  const startMin = OPEN_MIN + k * 30;
  if (startMin > LAST_STATUS_MIN) return null;
  return day * DAY - IST + startMin * 60;
}

export function isStatusBarMinute(minute: number): boolean {
  return minute >= READY_MIN && minute <= LAST_STATUS_MIN && (minute - OPEN_MIN) % 30 === 0;
}

export function sessionEndFromOpen(start915: number): number {
  return start915 + (CLOSE_MIN - OPEN_MIN) * 60;
}

export function toThirtyMinuteBars(candles: readonly FhbCandle[]): FhbCandle[] {
  const buckets = new Map<number, FhbCandle>();
  for (const c of candles) {
    if (!c || !Number.isFinite(c.time)) continue;
    const open = thirtyOpenUnix(c.time);
    if (open == null) continue;
    const cur = buckets.get(open);
    if (!cur) {
      buckets.set(open, { time: open, open: c.open, high: c.high, low: c.low, close: c.close });
    } else {
      cur.high = Math.max(cur.high, c.high);
      cur.low = Math.min(cur.low, c.low);
      cur.close = c.close;
    }
  }
  return [...buckets.values()].sort((a, b) => a.time - b.time);
}

type Phase =
  | { kind: "inside" }
  | { kind: "probe" | "break" | "accepted"; side: FhbSide };

function arrow(side: FhbSide): string {
  return side === "above" ? "↑" : "↓";
}

function opposite(side: FhbSide): FhbSide {
  return side === "above" ? "below" : "above";
}

function markerColor(kind: FhbKind, side: FhbSide | null): string {
  if (kind === "PROBE" || kind === "PROBE_BOTH") return FHB_COLORS.probe;
  if (kind === "FAILED") return FHB_COLORS.failed;
  if (kind === "BREAK") return FHB_COLORS.break;
  if (kind === "INSIDE") return FHB_COLORS.inside;
  if (side === "above") return FHB_COLORS.acceptedAbove;
  if (side === "below") return FHB_COLORS.acceptedBelow;
  return FHB_COLORS.probe;
}

/** One-letter chart marker; INSIDE → empty (no marker). */
export function markerLetter(kind: FhbKind): string {
  if (kind === "PROBE" || kind === "PROBE_BOTH") return "P";
  if (kind === "BREAK") return "B";
  if (kind === "ACCEPTED") return "A";
  if (kind === "FAILED") return "F";
  return "";
}

function makeMarker(time: number, kind: FhbKind, side: FhbSide | null): FhbMarker {
  let label: string;
  if (kind === "PROBE_BOTH") label = "PROBE ↕";
  else if (kind === "PROBE") label = `PROBE ${arrow(side!)}`;
  else if (kind === "BREAK") label = `BREAK ${arrow(side!)}`;
  else if (kind === "ACCEPTED") label = `ACCEPTED ${arrow(side!)}`;
  else if (kind === "FAILED") label = `FAILED ${arrow(side!)}`;
  else label = "INSIDE";
  return { time, kind, side, label, color: markerColor(kind, side) };
}

function closeSide(close: number, high: number, low: number): "above" | "below" | "inside" {
  if (close > high) return "above";
  if (close < low) return "below";
  return "inside";
}

export function uniqueAscendingMarkers(markers: readonly FhbMarker[]): FhbMarker[] {
  const map = new Map<number, FhbMarker>();
  for (const m of markers) {
    if (!m || !Number.isFinite(m.time)) continue;
    map.set(m.time, m);
  }
  return [...map.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([, m]) => m);
}

/** Next 30m close strictly after `asOf` (closes 10:45…15:15 IST). */
export function nextThirtyCloseAfter(asOf: number | null | undefined): number | null {
  if (asOf == null || !Number.isFinite(asOf)) return null;
  const day = Math.floor((asOf + IST) / DAY);
  for (let pass = 0; pass < 5; pass++) {
    const d = day + pass;
    for (let minute = READY_MIN + 30; minute <= LAST_STATUS_MIN + 30; minute += 30) {
      const close = d * DAY - IST + minute * 60;
      if (close > asOf) return close;
    }
  }
  return null;
}

/** HUD next-close text. After 15:15 IST → "next: tomorrow 10:45". */
export function formatNextCloseHint(asOf: number | null | undefined, nextClose: number | null): string | null {
  if (nextClose == null) return null;
  if (asOf != null && Number.isFinite(asOf)) {
    const minute = istMinuteOfDay(asOf);
    if (minute >= STUB_MIN || istDayKey(nextClose) !== istDayKey(asOf)) {
      return "next: tomorrow 10:45";
    }
  }
  return `next ${istHhmm(nextClose)}`;
}

/**
 * Boxes to paint + the "current" day (axis labels / HUD).
 * showPastDays=false → only current/replay day; past days otherwise faded in the UI.
 */
export function selectFhbPaintBoxes(
  boxes: readonly FhbDayBox[],
  opts: { showPastDays: boolean; asOf?: number | null },
): { paint: FhbDayBox[]; current: FhbDayBox | null } {
  if (!boxes.length) return { paint: [], current: null };
  const asOf = opts.asOf;
  const dayKey = asOf != null && Number.isFinite(asOf) ? istDayKey(asOf) : boxes[boxes.length - 1]!.day;
  const current =
    [...boxes].reverse().find((b) => b.day === dayKey) ?? boxes[boxes.length - 1] ?? null;
  if (!opts.showPastDays) return { paint: current ? [current] : [], current };
  return { paint: [...boxes], current };
}

/** Last bar time on the box day at or before session end — keeps LWC coords on-series. */
export function resolveSessionLineEnd(
  candles: readonly { time: number }[],
  startTime: number,
  sessionEndTime: number,
): number {
  let best = startTime;
  for (const c of candles) {
    if (!c || !Number.isFinite(c.time)) continue;
    if (c.time < startTime || c.time > sessionEndTime) continue;
    if (c.time > best) best = c.time;
  }
  return best;
}

export interface ComputeFirstHourBoxOpts {
  asOf?: number | null;
  skipOddSessions?: boolean;
  maxWidth?: number;
  /** Emit tiny INSIDE dots (default false = none). */
  showInside?: boolean;
}

export function computeFirstHourBox(
  candles: readonly FhbCandle[],
  opts: ComputeFirstHourBoxOpts = {},
): FirstHourBoxResult {
  const asOf = opts.asOf;
  const skipOdd = opts.skipOddSessions !== false;
  const maxWidth = opts.maxWidth ?? DEFAULT_MAX_WIDTH;
  const showInside = opts.showInside === true;
  const clipped =
    asOf == null || !Number.isFinite(asOf) ? candles : candles.filter((c) => c && c.time <= asOf!);
  const thirties = toThirtyMinuteBars(clipped);

  const byDay = new Map<string, FhbCandle[]>();
  for (const bar of thirties) {
    const key = istDayKey(bar.time);
    const list = byDay.get(key) ?? [];
    list.push(bar);
    byDay.set(key, list);
  }

  const boxes: FhbDayBox[] = [];
  for (const [day, bars] of [...byDay.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
    const candleA = bars.find((b) => istMinuteOfDay(b.time) === OPEN_MIN);
    if (!candleA) continue;
    if (skipOdd) {
      const first = bars[0];
      if (!first || istMinuteOfDay(first.time) !== OPEN_MIN) continue;
    }

    const startTime = candleA.time;
    const readyTime = startTime + 60 * 60; // 10:15
    const sessionEndTime = sessionEndFromOpen(startTime);
    const now = asOf ?? (clipped.length ? clipped[clipped.length - 1]!.time : startTime);
    const forming = now < readyTime;
    const candleB = bars.find((b) => istMinuteOfDay(b.time) === OPEN_MIN + 30);

    // Locked day requires both A and B; forming may use A alone (live) or A+B.
    if (!forming && !candleB) continue;

    const parts = candleB ? [candleA, candleB] : [candleA];
    const high = Math.max(...parts.map((p) => p.high));
    const low = Math.min(...parts.map((p) => p.low));
    if (!(high > low) || !Number.isFinite(high) || !Number.isFinite(low)) continue;
    const mid = (high + low) / 2;
    const width = high - low;
    const tooWide = width > maxWidth;

    const markers: FhbMarker[] = [];
    let phase: Phase = { kind: "inside" };
    let lastStatus: FhbKind | "FORMING" = forming ? "FORMING" : "INSIDE";
    let lastStatusTime: number | null = null;

    if (!forming) {
      for (const bar of bars) {
        const minute = istMinuteOfDay(bar.time);
        if (!isStatusBarMinute(minute)) continue;
        const barClose = bar.time + 30 * 60;
        if (asOf != null && asOf < barClose - 1) continue;

        const sideClose = closeSide(bar.close, high, low);
        const wickAbove = bar.high > high;
        const wickBelow = bar.low < low;
        let emitted: FhbMarker | null = null;

        if (phase.kind === "inside") {
          if (sideClose === "above") {
            emitted = makeMarker(bar.time, "BREAK", "above");
            phase = { kind: "break", side: "above" };
          } else if (sideClose === "below") {
            emitted = makeMarker(bar.time, "BREAK", "below");
            phase = { kind: "break", side: "below" };
          } else if (wickAbove && wickBelow) {
            emitted = makeMarker(bar.time, "PROBE_BOTH", null);
          } else if (wickAbove) {
            emitted = makeMarker(bar.time, "PROBE", "above");
            phase = { kind: "probe", side: "above" };
          } else if (wickBelow) {
            emitted = makeMarker(bar.time, "PROBE", "below");
            phase = { kind: "probe", side: "below" };
          } else if (showInside) {
            emitted = makeMarker(bar.time, "INSIDE", null);
          }
        } else if (sideClose === "inside") {
          emitted = makeMarker(bar.time, "FAILED", opposite(phase.side));
          phase = { kind: "inside" };
        } else if (sideClose === phase.side) {
          if (phase.kind === "break") {
            emitted = makeMarker(bar.time, "ACCEPTED", phase.side);
            phase = { kind: "accepted", side: phase.side };
          } else if (phase.kind === "probe") {
            emitted = makeMarker(bar.time, "BREAK", phase.side);
            phase = { kind: "break", side: phase.side };
          }
        } else {
          emitted = makeMarker(bar.time, "BREAK", sideClose);
          phase = { kind: "break", side: sideClose };
        }

        if (emitted) {
          markers.push(emitted);
          lastStatus = emitted.kind;
          lastStatusTime = barClose;
        } else {
          lastStatus = "INSIDE";
          lastStatusTime = barClose;
        }
      }
    }

    const uniq = uniqueAscendingMarkers(markers);
    boxes.push({
      day,
      high,
      low,
      mid,
      width: Math.round(width * 100) / 100,
      tooWide,
      startTime,
      readyTime,
      sessionEndTime,
      shadeEndTime: readyTime,
      forming,
      markers: uniq,
      lastStatus,
      lastStatusTime,
      lastStatusLabel:
        lastStatus === "FORMING"
          ? "Box forming"
          : uniq.length
            ? uniq[uniq.length - 1]!.label
            : "INSIDE",
    });
  }

  return {
    boxes,
    markers: uniqueAscendingMarkers(boxes.flatMap((box) => box.markers)),
    nextCloseTime: nextThirtyCloseAfter(asOf ?? (clipped.length ? clipped[clipped.length - 1]!.time : null)),
  };
}

export const FIRST_HOUR_BOX_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m"] as const;

export function firstHourBoxVisibleOn(tf: string): boolean {
  return (FIRST_HOUR_BOX_TIMEFRAMES as readonly string[]).includes(tf);
}

/** Alias kept for older fixtures / HUD that used a/b naming. */
export function boxLevels(box: Pick<FhbDayBox, "high" | "low" | "mid" | "width">): {
  a: number;
  b: number;
  mid: number;
  width: number;
} {
  return { a: box.high, b: box.low, mid: box.mid, width: box.width };
}
