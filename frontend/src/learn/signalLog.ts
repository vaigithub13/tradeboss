/** Saved Learn-signal results. The files live under data/learn/signals and stay untracked. */

import type { BreakdownRow } from "./signals";

export interface SavedStats {
  signals: number;
  tp: number;
  sl: number;
  expired: number;
  winRate: number | null;
  totalR: number;
  avgR: number | null;
  avgRNet?: number | null;
}

export interface SymbolBook {
  all: SavedStats;
  SWP: SavedStats;
  FVG: SavedStats;
  AMD: SavedStats;
  UNI?: SavedStats;
  OTE?: SavedStats;
  "FVG★"?: SavedStats;
  "FVG·"?: SavedStats;
  CRT75?: SavedStats;
  CRTD?: SavedStats;
  CRT?: SavedStats;
  breakdown?: BreakdownRow[];
}

export interface SignalsSummary {
  live: {
    since: string | null;
    bySymbol: Record<string, SymbolBook>;
  };
  history: {
    label: string;
    from: string;
    to: string;
    bySymbol: Record<string, SymbolBook>;
  } | null;
}

const EMPTY_STATS: SavedStats = {
  signals: 0, tp: 0, sl: 0, expired: 0, winRate: null, totalR: 0, avgR: null,
};

export function emptySymbolBook(): SymbolBook {
  return {
    all: { ...EMPTY_STATS },
    SWP: { ...EMPTY_STATS },
    FVG: { ...EMPTY_STATS },
    AMD: { ...EMPTY_STATS },
  };
}

export async function loadSignalSummary(): Promise<SignalsSummary | null> {
  try {
    const res = await fetch("/api/learn/signals");
    if (!res.ok) return null;
    return (await res.json()) as SignalsSummary;
  } catch {
    return null;
  }
}

export async function runSignalReview(): Promise<SignalsSummary> {
  const res = await fetch("/api/learn/signals/review", { method: "POST" });
  if (!res.ok) throw new Error("review failed");
  return (await res.json()) as SignalsSummary;
}
