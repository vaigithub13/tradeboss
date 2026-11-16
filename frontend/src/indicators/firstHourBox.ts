/**
 * First Hour Box (Strategy C chart overlay).
 *
 * Box A/B = high/low of the 09:15 + 09:45 IST 30m candles (ready at 10:15).
 * Status markers on 30m closes 10:45…15:15 (bars 10:15…14:45). No status on the 15:15 stub.
 *
 * State: INSIDE → PROBE / BREAK → ACCEPTED; close back inside → FAILED (opposite edge) → INSIDE.
 */

export interface FhbCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export type FhbSide = "above" | "below";
export type FhbKind = "PROBE" | "BREAK" | "ACCEPTED" | "FAILED" | "PROBE_BOTH";

export interface FhbMarker {
  /** unix seconds of the 30m bar start that produced this status */
  time: number;
  kind: FhbKind;
  /** for PROBE_BOTH null; FAILED points at the opposite edge */
  side: FhbSide | null;
  label: string;
  /** hex colour hint for the chart */
  color: string;
}

export interface FhbDayBox {
  day: string; // YYYY-MM-DD IST
  a: number; // high
  b: number; // low
  /** 09:15 unix */
  startTime: number;
  /** 10:15 unix — box ready */
  readyTime: number;
  /** last status bar start of the day (≤ 14:45), or readyTime if none */
  endTime: number;
  forming: boolean;
  markers: FhbMarker[];
}

export interface FirstHourBoxResult {
  boxes: FhbDayBox[];
  /** flattened markers, sorted unique ascending by time */
  markers: FhbMarker[];
}

const IST = 19_800;
const DAY = 86_400;
const OPEN_MIN = 9 * 60 + 15; // 09:15
const READY_MIN = 10 * 60 + 15; // 10:15
const LAST_STATUS_MIN = 14 * 60 + 45; // 14:45
const STUB_MIN = 15 * 60 + 15; // 15:15 — not a 30m status bar

const COLOR = {
  probe: "#f59e0b", // orange
  breakAbove: "#26a69a",
  breakBelow: "#ef5350",
  acceptedAbove: "#26a69a",
  acceptedBelow: "#ef5350",
  failed: "#94a3b8",
} as const;

export function formatInrPrice(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function istDayKey(unix: number): string {
  const day = Math.floor((unix + IST) / DAY);
  const utc = new Date((day * DAY - IST) * 1000);
  // format as IST calendar date via en-CA in Asia/Kolkata
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(utc);
}

export function istMinuteOfDay(unix: number): number {
  return Math.floor(((unix + IST) % DAY) / 60);
}

/** 30m bar open containing `unix`, anchored at 09:15; null outside session / on 15:15 stub for status. */
export function thirtyOpenUnix(unix: number): number | null {
  const day = Math.floor((unix + IST) / DAY);
  const minute = istMinuteOfDay(unix);
  if (minute < OPEN_MIN || minute >= STUB_MIN + 15) return null; // past 15:30
  if (minute >= STUB_MIN) return null; // 15:15 stub is not a 30m bar
  const k = Math.floor((minute - OPEN_MIN) / 30);
  const startMin = OPEN_MIN + k * 30;
  if (startMin > LAST_STATUS_MIN && startMin !== OPEN_MIN && startMin !== OPEN_MIN + 30) {
    // only 09:15…14:45
  }
  if (startMin > LAST_STATUS_MIN) return null;
  return day * DAY - IST + startMin * 60;
}

export function isStatusBarMinute(minute: number): boolean {
  return minute >= READY_MIN && minute <= LAST_STATUS_MIN && (minute - OPEN_MIN) % 30 === 0;
}

export function isFormingBarMinute(minute: number): boolean {
  return minute === OPEN_MIN || minute === OPEN_MIN + 30;
}

/** Aggregate any intraday source into 09:15-anchored 30m bars (drops 15:15 stub). */
export function toThirtyMinuteBars(candles: readonly FhbCandle[]): FhbCandle[] {
  const buckets = new Map<number, FhbCandle>();
  for (const c of candles) {
    if (!c || !Number.isFinite(c.time)) continue;
    const open = thirtyOpenUnix(c.time);
    if (open == null) continue;
    const cur = buckets.get(open);
    if (!cur) {
      buckets.set(open, {
        time: open,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      });
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
  if (kind === "PROBE" || kind === "PROBE_BOTH") return COLOR.probe;
  if (kind === "FAILED") return COLOR.failed;
  if (side === "above") return kind === "ACCEPTED" ? COLOR.acceptedAbove : COLOR.breakAbove;
  if (side === "below") return kind === "ACCEPTED" ? COLOR.acceptedBelow : COLOR.breakBelow;
  return COLOR.probe;
}

function makeMarker(time: number, kind: FhbKind, side: FhbSide | null): FhbMarker {
  let label: string;
  if (kind === "PROBE_BOTH") label = "PROBE ↕";
  else if (kind === "PROBE") label = `PROBE ${arrow(side!)}`;
  else if (kind === "BREAK") label = `BREAK ${arrow(side!)}`;
  else if (kind === "ACCEPTED") label = `ACCEPTED ${arrow(side!)}`;
  else label = `FAILED ${arrow(side!)}`;
  return { time, kind, side, label, color: markerColor(kind, side) };
}

function closeSide(close: number, a: number, b: number): "above" | "below" | "inside" {
  if (close > a) return "above";
  if (close < b) return "below";
  return "inside";
}

/** Deduplicate markers by time (last wins), keep ascending. */
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

export interface ComputeFirstHourBoxOpts {
  /** Replay / live cutoff: only use bars with time <= asOf (bar start). */
  asOf?: number | null;
  /** When true (default), skip days whose first bar is not 09:15 (muhurat / special). */
  skipOddSessions?: boolean;
}

/**
 * Compute first-hour boxes and status markers from candles of any supported intraday TF.
 * Pass `asOf` (replay cursor or last closed bar) so no future bars leak in.
 */
export function computeFirstHourBox(
  candles: readonly FhbCandle[],
  opts: ComputeFirstHourBoxOpts = {},
): FirstHourBoxResult {
  const asOf = opts.asOf;
  const skipOdd = opts.skipOddSessions !== false;
  const clipped =
    asOf == null || !Number.isFinite(asOf)
      ? candles
      : candles.filter((c) => c && c.time <= asOf!);
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
    const b915 = bars.find((b) => istMinuteOfDay(b.time) === OPEN_MIN);
    const b945 = bars.find((b) => istMinuteOfDay(b.time) === OPEN_MIN + 30);
    if (!b915 || !b945) continue; // A or B missing
    if (skipOdd) {
      const first = bars[0];
      if (!first || istMinuteOfDay(first.time) !== OPEN_MIN) continue; // muhurat / special
    }

    const a = Math.max(b915.high, b945.high);
    const b = Math.min(b915.low, b945.low);
    if (!(a > b) || !Number.isFinite(a) || !Number.isFinite(b)) continue;

    const startTime = b915.time;
    /** 10:15 IST — 09:45 bar close; box is ready and status may begin. */
    const readyTime = b945.time + 30 * 60;
    const now = asOf ?? (clipped.length ? clipped[clipped.length - 1]!.time : startTime);
    const forming = now < readyTime;

    const markers: FhbMarker[] = [];
    let phase: Phase = { kind: "inside" };
    let lastStatusStart = b945.time;

    if (!forming) {
      for (const bar of bars) {
        const minute = istMinuteOfDay(bar.time);
        if (!isStatusBarMinute(minute)) continue;
        // Only emit status once the 30m bar has closed (bar.time + 30m <= asOf+epsilon).
        // Using bar start: require asOf >= bar close, or asOf is null (full history).
        const barClose = bar.time + 30 * 60;
        if (asOf != null && asOf < barClose - 1) continue;

        lastStatusStart = bar.time;
        const sideClose = closeSide(bar.close, a, b);
        const wickAbove = bar.high > a;
        const wickBelow = bar.low < b;

        if (phase.kind === "inside") {
          if (sideClose === "above") {
            markers.push(makeMarker(bar.time, "BREAK", "above"));
            phase = { kind: "break", side: "above" };
          } else if (sideClose === "below") {
            markers.push(makeMarker(bar.time, "BREAK", "below"));
            phase = { kind: "break", side: "below" };
          } else if (wickAbove && wickBelow) {
            markers.push(makeMarker(bar.time, "PROBE_BOTH", null));
            // dual probe: stay INSIDE
          } else if (wickAbove) {
            markers.push(makeMarker(bar.time, "PROBE", "above"));
            phase = { kind: "probe", side: "above" };
          } else if (wickBelow) {
            markers.push(makeMarker(bar.time, "PROBE", "below"));
            phase = { kind: "probe", side: "below" };
          }
          continue;
        }

        // After PROBE / BREAK / ACCEPTED on a side
        if (sideClose === "inside") {
          const failSide = opposite(phase.side);
          markers.push(makeMarker(bar.time, "FAILED", failSide));
          phase = { kind: "inside" };
          continue;
        }

        if (sideClose === phase.side) {
          if (phase.kind === "break") {
            markers.push(makeMarker(bar.time, "ACCEPTED", phase.side));
            phase = { kind: "accepted", side: phase.side };
          }
          // already accepted / probe with close still outside same side: keep state
          else if (phase.kind === "probe") {
            // close outside after probe → upgrade to BREAK
            markers.push(makeMarker(bar.time, "BREAK", phase.side));
            phase = { kind: "break", side: phase.side };
          }
          continue;
        }

        // close on the opposite side → new BREAK that way
        markers.push(makeMarker(bar.time, "BREAK", sideClose));
        phase = { kind: "break", side: sideClose };
      }
    }

    boxes.push({
      day,
      a,
      b,
      startTime,
      readyTime,
      endTime: Math.max(lastStatusStart, b945.time),
      forming,
      markers: uniqueAscendingMarkers(markers),
    });
  }

  const allMarkers = uniqueAscendingMarkers(boxes.flatMap((box) => box.markers));
  return { boxes, markers: allMarkers };
}

/** Timeframes that may show the indicator. */
export const FIRST_HOUR_BOX_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m"] as const;

export function firstHourBoxVisibleOn(tf: string): boolean {
  return (FIRST_HOUR_BOX_TIMEFRAMES as readonly string[]).includes(tf);
}
