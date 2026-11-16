/** Pure helpers for the Paper dashboard: KPIs, daily matrix, equity, filters. */

import type { PaperStatus } from "../api/paper";
import type { PaperTradeRow } from "./trades";
import { ACTIVE_PAPER_SLOTS, isActivePaperSlot } from "./slots";

export interface PaperKpis {
  todayNet: number;
  weekNet: number;
  forwardNet: number;
  winPct: number | null;
  tradesToday: number;
  maxDrawdown: number | null;
}

export interface DashboardFilters {
  slot: string;
  from: string;
  to: string;
  side: "" | "CE" | "PE";
  outcome: "" | "win" | "loss";
}

export const DEFAULT_DASHBOARD_FILTERS: DashboardFilters = {
  slot: "",
  from: "",
  to: "",
  side: "",
  outcome: "",
};

/** ISO week key Monday..Sunday for an IST calendar date YYYY-MM-DD. */
export function istWeekKey(day: string): string {
  const parts = day.split("-").map(Number);
  const y = parts[0] ?? 1970;
  const m = parts[1] ?? 1;
  const d = parts[2] ?? 1;
  const utc = Date.UTC(y, m - 1, d);
  const date = new Date(utc);
  const dow = date.getUTCDay(); // 0 Sun
  const mondayOffset = dow === 0 ? -6 : 1 - dow;
  const monday = new Date(utc + mondayOffset * 86_400_000);
  return monday.toISOString().slice(0, 10);
}

export function todayIst(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

export function filterDashboardRows(rows: readonly PaperTradeRow[], f: DashboardFilters): PaperTradeRow[] {
  return rows.filter((r) => {
    if (!r) return false;
    if (f.slot && r.slot !== f.slot) return false;
    const date = r.date ?? "";
    if (f.from && (!date || date < f.from)) return false;
    if (f.to && (!date || date > f.to)) return false;
    if (f.side && r.direction !== f.side) return false;
    const net = numOr(r.net, 0);
    if (f.outcome === "win" && !(net > 0)) return false;
    if (f.outcome === "loss" && !(net <= 0)) return false;
    return true;
  });
}

export function holdMinutes(row: PaperTradeRow): number | null {
  const entry = numOrNull(row.entry_time);
  const exit = numOrNull(row.exit_time);
  if (entry == null || exit == null) return null;
  return Math.max(0, (exit - entry) / 60);
}

/** Max drawdown of a cumulative equity path (peak-to-trough of running net). */
export function maxDrawdown(netsInOrder: readonly number[]): number | null {
  if (!netsInOrder.length) return null;
  let equity = 0;
  let peak = 0;
  let dd = 0;
  for (const n of netsInOrder) {
    equity += n;
    peak = Math.max(peak, equity);
    dd = Math.max(dd, peak - equity);
  }
  return Math.round(dd * 100) / 100;
}

export function computeKpis(
  rows: readonly PaperTradeRow[],
  opts: { today: string; weekNets?: Record<string, number> },
): PaperKpis {
  const active = (rows ?? []).filter((r) => r && isActivePaperSlot(String(r.slot ?? "")));
  const todayRows = active.filter((r) => r.date === opts.today);
  const todayNet = round2(todayRows.reduce((s, r) => s + numOr(r.net, 0), 0));
  const tradesToday = todayRows.length;
  const wins = active.filter((r) => numOr(r.net, 0) > 0).length;
  const forwardNet = round2(active.reduce((s, r) => s + numOr(r.net, 0), 0));
  const winPct = active.length ? round2((100 * wins) / active.length) : null;
  const ordered = [...active].sort((a, b) => numOr(a.entry_time, 0) - numOr(b.entry_time, 0));
  const dd = maxDrawdown(ordered.map((r) => numOr(r.net, 0)));
  const weekKey = istWeekKey(opts.today);
  let weekNet = 0;
  if (opts.weekNets) {
    weekNet = round2(Object.values(opts.weekNets).reduce((s, v) => s + numOr(v, 0), 0));
  } else {
    weekNet = round2(
      active.filter((r) => r.date && istWeekKey(r.date) === weekKey).reduce((s, r) => s + numOr(r.net, 0), 0),
    );
  }
  return { todayNet, weekNet, forwardNet, winPct, tradesToday, maxDrawdown: dd };
}

export interface DailyCell {
  date: string;
  bySlot: Record<string, number>;
  total: number;
}

/** Daily P&L matrix for active slots (dates descending). */
export function dailyPnLMatrix(rows: readonly PaperTradeRow[], slots: readonly string[] = ACTIVE_PAPER_SLOTS): DailyCell[] {
  const map = new Map<string, Record<string, number>>();
  for (const r of rows ?? []) {
    if (!r || !slots.includes(r.slot) || !r.date) continue;
    const row = map.get(r.date) ?? Object.fromEntries(slots.map((s) => [s, 0]));
    row[r.slot] = round2((row[r.slot] ?? 0) + numOr(r.net, 0));
    map.set(r.date, row);
  }
  return [...map.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([date, bySlot]) => ({
      date,
      bySlot,
      total: round2(Object.values(bySlot).reduce((s, v) => s + v, 0)),
    }));
}

export interface EquityPoint {
  time: number; // unix seconds (exit or entry)
  date: string;
  bySlot: Record<string, number>;
}

/** Trade timestamp for equity (exit preferred). Null when missing/NaN. */
export function tradeEquityTime(row: Pick<PaperTradeRow, "exit_time" | "entry_time">): number | null {
  return numOrNull(row.exit_time) ?? numOrNull(row.entry_time);
}

/** Cumulative net ₹ per slot over time. Same-second exits are merged (LWC needs unique ascending times). */
export function equitySeries(rows: readonly PaperTradeRow[], slots: readonly string[] = ACTIVE_PAPER_SLOTS): EquityPoint[] {
  const ordered = [...(rows ?? [])]
    .filter((r) => r && slots.includes(r.slot) && tradeEquityTime(r) != null)
    .sort((a, b) => (tradeEquityTime(a) ?? 0) - (tradeEquityTime(b) ?? 0));
  const running: Record<string, number> = Object.fromEntries(slots.map((s) => [s, 0]));
  const out: EquityPoint[] = [];
  for (const r of ordered) {
    const time = tradeEquityTime(r);
    if (time == null) continue;
    running[r.slot] = round2((running[r.slot] ?? 0) + numOr(r.net, 0));
    const point: EquityPoint = {
      time,
      date: r.date ?? "",
      bySlot: { ...running },
    };
    const last = out[out.length - 1];
    if (last && last.time === time) {
      out[out.length - 1] = point;
    } else {
      out.push(point);
    }
  }
  return out;
}

export interface SlotStats {
  slot: string;
  todayNet: number;
  totalNet: number;
  trades: number;
  wins: number;
  winPct: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  exits: Record<string, number>;
}

export function slotStats(rows: readonly PaperTradeRow[], slot: string, today: string): SlotStats {
  const all = (rows ?? []).filter((r) => r && r.slot === slot);
  const todayRows = all.filter((r) => r.date === today);
  const wins = all.filter((r) => numOr(r.net, 0) > 0);
  const losses = all.filter((r) => numOr(r.net, 0) <= 0);
  const exits: Record<string, number> = {};
  for (const r of all) {
    const raw = r.exit_reason ?? "other";
    // trail fills still report as stop from the API; keep trail chip if ever present
    const key = raw === "trail" ? "trail" : raw;
    exits[key] = (exits[key] ?? 0) + 1;
  }
  return {
    slot,
    todayNet: round2(todayRows.reduce((s, r) => s + numOr(r.net, 0), 0)),
    totalNet: round2(all.reduce((s, r) => s + numOr(r.net, 0), 0)),
    trades: all.length,
    wins: wins.length,
    winPct: all.length ? round2((100 * wins.length) / all.length) : null,
    avgWin: wins.length ? round2(wins.reduce((s, r) => s + numOr(r.net, 0), 0) / wins.length) : null,
    avgLoss: losses.length ? round2(losses.reduce((s, r) => s + numOr(r.net, 0), 0) / losses.length) : null,
    exits,
  };
}

export function todaySummaryFromSlots(slots: readonly PaperStatus[]): {
  net: number;
  trades: number;
  wins: number;
  losses: number;
  winPct: number | null;
} {
  let net = 0;
  let trades = 0;
  let wins = 0;
  for (const s of slots ?? []) {
    if (!s || !isActivePaperSlot(String(s.slot ?? ""))) continue;
    net += numOr(s.summary?.net, 0);
    trades += numOr(s.summary?.trades, 0);
    wins += numOr(s.summary?.wins, 0);
  }
  const losses = Math.max(0, trades - wins);
  return {
    net: round2(net),
    trades,
    wins,
    losses,
    winPct: trades ? round2((100 * wins) / trades) : null,
  };
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

function numOr(v: unknown, fallback: number): number {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : Number.NaN;
  return Number.isFinite(n) ? n : fallback;
}

function numOrNull(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : Number.NaN;
  return Number.isFinite(n) ? n : null;
}

/** Live position fields for cards; missing LTP shows null (API does not expose bid). */
export function livePositionView(status: PaperStatus | null | undefined): {
  side: string;
  strike: string;
  entry: number | null;
  ltp: number | null;
  unrealised: number | null;
  stop: number | null;
  target: number | null;
  contract: string;
} | null {
  const pos = status?.position;
  if (!pos) return null;
  const contract = String(pos.symbol ?? pos.contract ?? status?.mark?.symbol ?? "");
  const parts = contract.split(/\s+/).filter(Boolean);
  const strike = parts.length >= 2 ? parts[1] ?? "—" : "—";
  const side = parts.length >= 3 ? parts[2] ?? "—" : "—";
  const entryRaw = pos.entry && typeof pos.entry === "object" ? pos.entry.price : undefined;
  return {
    side,
    strike,
    entry: numOrNull(entryRaw ?? pos.entry_price),
    ltp: null, // not on PaperMark / open position — show "—" in UI
    unrealised: numOrNull(status?.mark?.net),
    stop: numOrNull(pos.levels?.premium_stop),
    target: numOrNull(pos.levels?.premium_target),
    contract,
  };
}

/** Safe money/number display helper for dashboard cells. */
export function fmtNum(v: unknown, digits = 2): string {
  const n = numOrNull(v);
  return n == null ? "—" : n.toFixed(digits);
}
