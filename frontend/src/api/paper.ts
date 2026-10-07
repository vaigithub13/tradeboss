import { getJson, postJson } from "./client";
import type { PaperMark, PaperSignal } from "../paper/present";

/** One paper slot ("1" or "2"): its own strategy, position and P&L. */
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
  summary: { trades: number; wins: number; net: number; modelled_legs: number } | null;
  position: { symbol: string } | null;
  mark: PaperMark | null;
}

/** Both slots; `state` is running when either runs. */
export interface PaperDeskStatus {
  state: "running" | "stopped" | "disabled";
  slots: PaperStatus[];
}

export const PAPER_SLOTS = ["1", "2"] as const;

export const fetchPaperStatus = (): Promise<PaperDeskStatus> => getJson<PaperDeskStatus>("/api/paper/status");

export const startPaper = (slot: string, strategy: string, params: Record<string, unknown>): Promise<PaperStatus> =>
  postJson<PaperStatus>("/api/paper/start", { strategy, params, slot });

export const stopPaper = (slot: string): Promise<PaperStatus> =>
  postJson<PaperStatus>(`/api/paper/stop?slot=${encodeURIComponent(slot)}`, {});
