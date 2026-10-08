import { getJson, postJson } from "./client";
import type { PaperMark, PaperSignal } from "../paper/present";
import type { ExitCounts, ReportRow } from "../report/present";
import type { PaperTradeRow } from "../paper/trades";

/** One paper slot ("1".."4"): its own strategy, position and P&L. */
export interface PaperStatus {
  slot: string;
  state: "running" | "stopped" | "disabled";
  ended_by: string | null;
  day: string | null;
  strategy: string | null;
  params: Record<string, unknown>;
  wanted_keys: string[];
  signals: PaperSignal[];
  trades: { symbol: string; net: number; exit_reason: string }[];
  summary: { trades: number; wins: number; net: number; modelled_legs: number; exits?: ExitCounts } | null;
  position: { symbol: string } | null;
  mark: PaperMark | null;
  /** one row per closed trade (backend app/exits/report.py) */
  report?: ReportRow[];
  exit_rule?: { kind: string; stop: number; target: number } | null;
  /** live, or replay: a run started after the day's session ended (kept apart, never counted) */
  source?: "live" | "replay";
}

export interface PaperWeek {
  week: string;
  totals: { trades: number; net: number };
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

export const startPaper = (slot: string, strategy: string, params: Record<string, unknown>): Promise<PaperStatus> =>
  postJson<PaperStatus>("/api/paper/start", { strategy, params, slot });

export const fetchPaperWeek = (slot: string, day: string): Promise<PaperWeek> =>
  getJson<PaperWeek>(`/api/paper/week?slot=${encodeURIComponent(slot)}&day=${encodeURIComponent(day)}`);

export const stopPaper = (slot: string): Promise<PaperStatus> =>
  postJson<PaperStatus>(`/api/paper/stop?slot=${encodeURIComponent(slot)}`, {});

/** Every paper trade over the slots, one row each (live days only). */
export const fetchPaperTrades = (): Promise<{ rows: PaperTradeRow[] }> => getJson<{ rows: PaperTradeRow[] }>("/api/paper/trades");
