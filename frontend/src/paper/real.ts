/** Manual real-trades log helpers (backend app/paper/real.py). Pure: no paper engine. */

import type { ReportRow } from "../report/present";

export const DEFAULT_ACCOUNT_STOP = 5000;
export const REAL_EXIT_REASONS = ["target", "stop", "reversal", "square-off", "other"] as const;
export type RealExitReason = (typeof REAL_EXIT_REASONS)[number];

export interface RealTradeLog {
  key: string;
  slot: string;
  day: string;
  fill_time: number;
  contract?: string | null;
  entry_time: number;
  entry_price: number;
  exit_time: number;
  exit_price: number;
  exit_reason: RealExitReason | string;
  charges: number;
  units: number;
  gross: number;
  net: number;
  logged_at?: string;
}

export interface RealVsPaper {
  entry_price_diff: number;
  exit_price_diff: number;
  entry_delay_s: number;
  exit_delay_s: number | null;
  net_diff: number;
  gross: number;
  net: number;
  charges: number;
}

export interface AccountStopProgress {
  account_stop: number;
  real_net: number;
  loss_used: number;
  fraction: number;
  reached: boolean;
}

export interface RealTotals {
  logged: number;
  day: { date: string | null; trades: number; real_net: number; paper_net: number; net_diff: number };
  running: { trades: number; real_net: number; paper_net: number; net_diff: number };
  stop: AccountStopProgress;
}

export interface OrderHelper {
  contract: string;
  quantity: number;
  lots: number;
  lot_size: number;
  side: string;
  entry_premium: number;
  stop_trigger: number;
  stop_limit: number;
  target_limit: number;
}

/** Open paper position shape from GET /api/paper/status (slot snapshot `open`). */
export interface PaperOpenPosition {
  symbol?: string;
  contract?: string;
  units?: number;
  lots?: number;
  lot_size?: number;
  entry?: { price?: number };
  entry_price?: number;
  levels?: { premium_stop?: number | null; premium_target?: number | null } | null;
}

export function paperTradeKey(slot: string, day: string, fillTime: number): string {
  return `${slot}:${day}:${Math.trunc(fillTime)}`;
}

export function realGross(entryPrice: number, exitPrice: number, units: number): number {
  return Math.round((exitPrice - entryPrice) * units * 100) / 100;
}

export function realNet(entryPrice: number, exitPrice: number, units: number, charges: number): number {
  return Math.round((realGross(entryPrice, exitPrice, units) - charges) * 100) / 100;
}

export function compareReal(paper: Pick<ReportRow, "entry_premium" | "exit_premium" | "net" | "fill_time" | "exit_time"> & { units: number },
  real: Pick<RealTradeLog, "entry_price" | "exit_price" | "charges" | "entry_time" | "exit_time" | "units">): RealVsPaper {
  const units = real.units || paper.units;
  const net = realNet(real.entry_price, real.exit_price, units, real.charges);
  return {
    entry_price_diff: round2(real.entry_price - paper.entry_premium),
    exit_price_diff: round2(real.exit_price - (paper.exit_premium ?? 0)),
    entry_delay_s: real.entry_time - paper.fill_time,
    exit_delay_s: paper.exit_time == null ? null : real.exit_time - paper.exit_time,
    net_diff: round2(net - (paper.net ?? 0)),
    gross: realGross(real.entry_price, real.exit_price, units),
    net,
    charges: real.charges,
  };
}

export function orderHelperFromOpen(open: PaperOpenPosition | null | undefined, tick = 0.05): OrderHelper | null {
  if (!open) return null;
  const stop = open.levels?.premium_stop;
  const target = open.levels?.premium_target;
  if (stop == null || target == null) return null;
  const qty = open.units ?? 0;
  if (qty < 1) return null;
  const entry = open.entry?.price ?? open.entry_price ?? 0;
  return {
    contract: open.symbol ?? open.contract ?? "",
    quantity: qty,
    lots: open.lots ?? 1,
    lot_size: open.lot_size ?? qty,
    side: "BUY",
    entry_premium: entry,
    stop_trigger: stop,
    stop_limit: round2(stop - tick),
    target_limit: target,
  };
}

export function accountStopProgress(realNetTotal: number, accountStop = DEFAULT_ACCOUNT_STOP): AccountStopProgress {
  const stop = Math.abs(accountStop) || DEFAULT_ACCOUNT_STOP;
  const loss = Math.max(0, -realNetTotal);
  const fraction = stop ? Math.min(1, loss / stop) : 0;
  return {
    account_stop: stop,
    real_net: round2(realNetTotal),
    loss_used: round2(loss),
    fraction: round2(fraction * 10000) / 10000,
    reached: loss >= stop - 1e-9,
  };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/** IST wall time for `<input type="datetime-local">` value. */
export function toDatetimeLocalValue(unixSeconds: number): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).formatToParts(new Date(unixSeconds * 1000));
  const g = (t: string) => parts.find((p) => p.type === t)?.value ?? "00";
  return `${g("year")}-${g("month")}-${g("day")}T${g("hour")}:${g("minute")}:${g("second")}`;
}

/** Parse a datetime-local string as Asia/Kolkata wall time → unix seconds. */
export function fromDatetimeLocalValue(value: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim());
  if (!m) return null;
  const [y, mo, d, h, mi, s] = [m[1], m[2], m[3], m[4], m[5], m[6] ?? "0"].map(Number);
  // IST = UTC+5:30 → UTC = IST − 5:30
  const utc = Date.UTC(y!, mo! - 1, d!, h! - 5, mi! - 30, s!);
  return Math.floor(utc / 1000);
}

export function formatDelay(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const sign = seconds > 0 ? "+" : "";
  return `${sign}${seconds}s`;
}

export function formatSigned(n: number | null | undefined, digits = 2): string {
  if (n == null) return "—";
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}`;
}
