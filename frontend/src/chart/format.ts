import type { Candle, Timeframe } from "../api/client";

const IST = "Asia/Kolkata";

const parts = new Intl.DateTimeFormat("en-GB", {
  timeZone: IST,
  weekday: "short",
  day: "2-digit",
  month: "short",
  year: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const tickParts = new Intl.DateTimeFormat("en-GB", {
  timeZone: IST,
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function pick(f: Intl.DateTimeFormat, unixSeconds: number): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of f.formatToParts(new Date(unixSeconds * 1000))) {
    out[p.type] = p.type === "month" ? p.value.slice(0, 3) : p.value; // newer ICU says "Sept"
  }
  return out;
}

export function isIntraday(tf: Timeframe): boolean {
  return tf !== "1D" && tf !== "1W";
}

/** Crosshair / tooltip time label, always IST. e.g. "Mon 01 Jan '24 09:15" (date only for 1D/1W). */
export function formatCrosshairTime(unixSeconds: number, tf: Timeframe): string {
  const p = pick(parts, unixSeconds);
  const date = `${p["weekday"]} ${p["day"]} ${p["month"]} '${p["year"]}`;
  return isIntraday(tf) ? `${date} ${p["hour"]}:${p["minute"]}` : date;
}

export type TickKind = "year" | "month" | "day" | "time";

/** Time-axis tick label in IST. */
export function formatTick(unixSeconds: number, kind: TickKind): string {
  const p = pick(tickParts, unixSeconds);
  switch (kind) {
    case "year":
      return p["year"] ?? "";
    case "month":
      return p["month"] ?? "";
    case "day":
      return p["day"] ?? "";
    case "time":
      return `${p["hour"]}:${p["minute"]}`;
  }
}

const price = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const volume = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export interface LegendValues {
  open: string;
  high: string;
  low: string;
  close: string;
  /** change vs previous close (or vs open for the first candle) */
  change: string;
  changePct: string;
  up: boolean;
  volume: string | null;
}

const signed = (n: number, f: Intl.NumberFormat): string => `${n >= 0 ? "+" : "-"}${f.format(Math.abs(n))}`;

export function legendValues(
  c: Candle,
  prevClose: number | null,
  showVolume: boolean,
): LegendValues {
  const base = prevClose ?? c.open;
  const change = c.close - base;
  const pct = base !== 0 ? (change / base) * 100 : 0;
  return {
    open: price.format(c.open),
    high: price.format(c.high),
    low: price.format(c.low),
    close: price.format(c.close),
    change: signed(change, price),
    changePct: `${signed(pct, price)}%`,
    up: change >= 0,
    volume: showVolume ? volume.format(c.volume) : null,
  };
}
