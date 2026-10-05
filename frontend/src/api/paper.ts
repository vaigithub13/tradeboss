import { getJson, postJson } from "./client";
import type { PaperMark, PaperSignal } from "../paper/present";

export interface PaperStatus {
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

export const fetchPaperStatus = (): Promise<PaperStatus> => getJson<PaperStatus>("/api/paper/status");

export const startPaper = (strategy: string): Promise<PaperStatus> =>
  postJson<PaperStatus>("/api/paper/start", { strategy, params: {} });

export const stopPaper = (): Promise<PaperStatus> => postJson<PaperStatus>("/api/paper/stop", {});
