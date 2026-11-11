import { deleteJson, getJson, postJson, putJson } from "./client";
import type { PaperMark, PaperSignal } from "../paper/present";
import type {
  AccountStopProgress,
  OrderHelper,
  PaperOpenPosition,
  RealTotals,
  RealTradeLog,
  RealVsPaper,
} from "../paper/real";
import type { ExitCounts, ReportRow } from "../report/present";
import type { PaperTradeRow } from "../paper/trades";

/** One paper slot ("1".."4"): its own strategy, position and P&L. */
export interface PaperStatus {
  slot: string;
  state: "running" | "starting" | "stopped" | "disabled";
  ended_by: string | null;
  day: string | null;
  strategy: string | null;
  params: Record<string, unknown>;
  wanted_keys: string[];
  signals: PaperSignal[];
  trades: { symbol: string; net: number; exit_reason: string }[];
  summary: {
    trades: number; wins: number; net: number; modelled_legs: number; exits?: ExitCounts;
    /** the net had resting orders taken the exits paper's ticks missed (backend app/paper/shadow.py) */
    shadow_net?: number;
    shadow_diff?: number;
  } | null;
  position: PaperOpenPosition | null;
  mark: PaperMark | null;
  /** one row per closed trade (backend app/exits/report.py) */
  report?: ReportRow[];
  exit_rule?: { kind: string; stop: number; target: number } | null;
  /** live, or replay: a run started after the day's session ended (kept apart, never counted) */
  source?: "live" | "replay";
  /** who started the slot: launchd paper_autostart, or the Paper panel */
  start_source?: "auto" | "hand" | null;
}

export interface PaperWeek {
  week: string;
  totals: { trades: number; net: number; shadow_net?: number; shadow_diff?: number };
  exits: ExitCounts;
  excluded: string[];
}

/** Both slots; `state` is running when either runs. */
export interface PaperDeskStatus {
  state: "running" | "stopped" | "disabled";
  slots: PaperStatus[];
}

export const PAPER_SLOTS = ["1", "2", "3", "4"] as const;

export const fetchPaperStatus = (): Promise<PaperDeskStatus> => getJson<PaperDeskStatus>("/api/paper/status");

export const startPaper = (
  slot: string,
  strategy: string,
  params: Record<string, unknown>,
  startSource: "auto" | "hand" = "hand",
): Promise<PaperStatus> =>
  postJson<PaperStatus>("/api/paper/start", { strategy, params, slot, start_source: startSource });

export const fetchPaperWeek = (slot: string, day: string): Promise<PaperWeek> =>
  getJson<PaperWeek>(`/api/paper/week?slot=${encodeURIComponent(slot)}&day=${encodeURIComponent(day)}`);

export const stopPaper = (slot: string): Promise<PaperStatus> =>
  postJson<PaperStatus>(`/api/paper/stop?slot=${encodeURIComponent(slot)}`, {});

/** Every paper trade over the slots, one row each (live days only). */
export const fetchPaperTrades = (): Promise<{ rows: PaperTradeRow[] }> => getJson<{ rows: PaperTradeRow[] }>("/api/paper/trades");

export interface PaperTradeWithReal extends PaperTradeRow {
  paper_key: string;
  real: RealTradeLog | null;
  real_vs_paper: RealVsPaper | null;
}

export interface RealTradesResponse {
  rows: PaperTradeWithReal[];
  totals: RealTotals;
  settings: { account_stop: number };
}

export const fetchRealTrades = (opts?: { slot?: string; from?: string; to?: string }): Promise<RealTradesResponse> => {
  const q = new URLSearchParams();
  if (opts?.slot) q.set("slot", opts.slot);
  if (opts?.from) q.set("from", opts.from);
  if (opts?.to) q.set("to", opts.to);
  const qs = q.toString();
  return getJson<RealTradesResponse>(`/api/paper/real${qs ? `?${qs}` : ""}`);
};

export const upsertRealTrade = (body: {
  slot: string;
  day: string;
  fill_time: number;
  contract?: string | null;
  entry_time: number;
  entry_price: number;
  exit_time: number;
  exit_price: number;
  exit_reason: string;
  charges: number;
  units: number;
}): Promise<{ trade: RealTradeLog }> => putJson("/api/paper/real", body);

export const deleteRealTrade = (key: string): Promise<{ deleted: string }> =>
  deleteJson(`/api/paper/real?key=${encodeURIComponent(key)}`);

export const fetchRealSettings = (): Promise<{ account_stop: number }> => getJson("/api/paper/real/settings");

export const saveRealSettings = (account_stop: number): Promise<{ account_stop: number }> =>
  putJson("/api/paper/real/settings", { account_stop });

export const fetchOrderHelper = (slot = "3"): Promise<{ slot: string; helper: OrderHelper | null; state: string }> =>
  getJson(`/api/paper/real/order-helper?slot=${encodeURIComponent(slot)}`);

export type { AccountStopProgress, OrderHelper, RealTotals };
