import { getJson, putJson, postJson } from "./client";

export interface ScannerStatus {
  now: string;
  trading_day: boolean;
  universe: string;
  universe_label: string;
  universes: string[];
  unmapped: string[];
  net: number;
  trades_used: number;
  max_trades: number;
  loss_used: number;
  daily_loss_limit: number;
  minutes_to_cutoff: number | null;
  minutes_to_flat: number | null;
  auto_trade: boolean;
  alerts: ScannerAlert[];
  open: ScannerOpen[];
}

export interface ScannerAlert {
  id: string;
  kind: string;
  message: string;
}

export interface ScannerOpen {
  symbol: string;
  side: string;
  qty: number;
  entry: number;
  stop: number;
  target: number;
}

export interface ScannerSignal {
  id: string;
  symbol: string;
  folder?: string;
  setup: string;
  side: string;
  status: string;
  decision?: string;
  reasons?: string[];
  news?: string;
  verdict?: string | null;
  entry?: number | null;
  stop?: number | null;
  target?: number | null;
  qty?: number;
  rupee_risk?: number | null;
  rupee_reward?: number | null;
  capital?: number | null;
  rr?: number | null;
  target_pct?: number | null;
  stop_pct?: number | null;
  open_pct?: number | null;
  avg_rupees?: number | null;
  win_rate?: number | null;
  history_source?: "stock" | "pooled" | string;
  avg_r?: number | null;
  fires?: number;
  wins?: number;
  losses?: number;
  neither?: number;
  occurrences?: { day: string; side: string; r: number; reason: string }[];
  histogram?: { target: number; stop: number; open: number };
  change_pct?: number | null;
  vwap_dist_pct?: number | null;
  rvol?: number | null;
  vs_nifty?: number | null;
  sector?: string;
  range_high?: number | null;
  range_low?: number | null;
  trigger_time?: number;
  resistance?: number | null;
  support?: number | null;
  proven?: boolean;
  note?: string;
}

export interface HeatRow {
  symbol: string;
  sector: string;
  change_pct: number | null;
  vwap_dist_pct: number | null;
  rvol: number | null;
  vs_nifty: number | null;
}

export interface ScannerSettings {
  account_size: number;
  max_loss_per_trade: number;
  daily_loss_limit: number;
  max_trades_per_day: number;
  max_open_positions: number;
  max_capital_per_trade: number;
  min_rr: number;
  entry_mode: string;
  orb_stop: string;
  target_mode: string;
  trail_breakeven: boolean;
  tm_paths: number;
  tm_seed: number;
  tm_edge_points: number;
  tm_min_win_rate: number;
  tm_min_fires: number;
  universe: string;
  only_proven: boolean;
  auto_trade: boolean;
  replay_ready?: boolean;
  replay_reason?: string;
  live_subscribe: boolean;
  slippage_ticks: number;
  tick_size: number;
  setups: Record<string, boolean>;
}

export interface ScannerTrade {
  symbol: string;
  setup: string;
  side: string;
  qty: number;
  entry: number;
  exit: number;
  r: number;
  net: number;
  exit_reason: string;
  tm_target_pct?: number | null;
  tm_stop_pct?: number | null;
  verdict?: string | null;
}

export interface ScannerSnapshot {
  status: ScannerStatus;
  signals: ScannerSignal[];
  heatmap: HeatRow[];
  trades: ScannerTrade[];
  report: Record<string, unknown> | null;
  universes: {
    universes: Record<string, { label: string; symbols: string[] }>;
    selected: string;
  };
  rankings: { as_of: string | null; pairs: RankPair[] };
}

export function fetchScannerSnapshot(signal?: AbortSignal): Promise<ScannerSnapshot> {
  return getJson("/api/scanner/snapshot", signal);
}

export function fetchScannerStatus(signal?: AbortSignal): Promise<ScannerStatus> {
  return getJson("/api/scanner/status", signal);
}

export function fetchScannerSignals(signal?: AbortSignal): Promise<{ signals: ScannerSignal[] }> {
  return getJson("/api/scanner/signals", signal);
}

export function fetchScannerHeatmap(signal?: AbortSignal): Promise<{ rows: HeatRow[] }> {
  return getJson("/api/scanner/heatmap", signal);
}

export function fetchScannerSettings(signal?: AbortSignal): Promise<ScannerSettings> {
  return getJson("/api/scanner/settings", signal);
}

export function saveScannerSettings(body: Partial<ScannerSettings>): Promise<ScannerSettings> {
  return putJson("/api/scanner/settings", body);
}

export function scanNow(): Promise<ScannerStatus> {
  return postJson("/api/scanner/scan", {});
}

export function fetchScannerTrades(signal?: AbortSignal): Promise<{ trades: ScannerTrade[] }> {
  return getJson("/api/scanner/trades", signal);
}

export function fetchScannerReport(signal?: AbortSignal): Promise<{ report: Record<string, unknown> | null }> {
  return getJson("/api/scanner/report", signal);
}

export interface RankPair {
  symbol: string;
  setup: string;
  trades: number;
  win_rate: number | null;
  avg_r: number | null;
  profit_factor: number | null;
  max_drawdown: number | null;
  net: number;
}

export function fetchRankings(signal?: AbortSignal): Promise<{ as_of: string | null; pairs: RankPair[] }> {
  return getJson("/api/scanner/rankings", signal);
}

export function fetchUniverses(signal?: AbortSignal): Promise<{
  universes: Record<string, { label: string; symbols: string[] }>;
  selected: string;
}> {
  return getJson("/api/scanner/universes", signal);
}
