import { getJson, postJson } from "./client";
import type { RunConfig } from "../backtest/present";

export interface StrategyField {
  type: "int" | "float" | "str";
  default: number | string;
  min?: number;
  choices?: string[];
}

export interface StrategySpec {
  name: string;
  params: Record<string, StrategyField>;
}

export interface EquityPoint {
  exit_time: number;
  equity: number;
  drawdown: number;
}

export interface OptionLeg {
  contract: { kind: string; strike: number; expiry: string; lot_size: number };
  entry_premium: number;
  exit_premium: number;
  entry_fill: number;
  exit_fill: number;
  entry_source: string;
  exit_source: string;
  gross_pnl: number;
  charges_buy: Record<string, number>;
  charges_sell: Record<string, number>;
  charges_total: number;
  slippage_cost: number;
  net_pnl: number;
  flags: string[];
  dte: number | null;
}

export interface ResultTrade {
  id: number;
  direction: string;
  entry_time: number;
  exit_time: number;
  entry_price: number;
  exit_price: number;
  gross_pnl: number;
  charges: Record<string, number>;
  charges_total: number;
  slippage_cost: number;
  net_pnl: number;
  exit_reason: string;
  option?: OptionLeg;
}

export interface SideSummary {
  net_pnl: number;
  trades: number;
  win_rate: number | null;
  max_drawdown: number;
  points?: number;
  gross_pnl: number | null;
  total_charges: number | null;
  total_slippage: number | null;
  real_fills?: number;
  modelled_fills?: number;
  real_pnl?: number | null;
  modelled_pnl?: number | null;
}

export interface WalkWindow {
  window: number;
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  params: Record<string, number | string> | null;
  reason: string | null;
  test: { net_pnl: number; max_drawdown: number; trades: number; flat: boolean };
}

export interface RunResult {
  kind?: string;
  holdout?: { start: string; end: string; peeks: number };
  windows?: WalkWindow[];
  degradation?: { window: number; train_net_per_trade: number; test_net_per_trade: number; ratio: number }[];
  param_changes?: number;
  summary: { index: SideSummary; option: SideSummary | null };
  equity: { index: EquityPoint[]; option: EquityPoint[] };
  trades: ResultTrade[];
  breakdowns: {
    weekday: Record<string, { trades: number; net_pnl: number }>;
    time_of_day: Record<string, { trades: number; net_pnl: number }>;
    dte: Record<string, { trades: number; net_pnl: number }>;
    flags: Record<string, { flagged: { trades: number; net_pnl: number }; unflagged: { trades: number; net_pnl: number } }>;
  };
  mode: "index" | "options";
}

export interface BacktestRun {
  id: string;
  created_at: string;
  status: "queued" | "running" | "done" | "failed";
  config: RunConfig;
  run_id: string | null;
  overlay_run_id: string | null;
  model_version: string | null;
  cost_rows: { effective_from: string }[];
  data_hash: string | null;
  git_commit: string;
  git_dirty: boolean;
  warnings: string[];
  error: string | null;
  progress: { phase: string; done: number; total: number };
  reproduced: boolean | null;
  parent_id: string | null;
  summary?: RunResult["summary"] | null;
  result?: RunResult | null;
}

export function fetchStrategies(): Promise<{ strategies: StrategySpec[] }> {
  return getJson("/api/backtests/strategies");
}

export function startBacktest(config: RunConfig): Promise<{ id: string }> {
  return postJson("/api/backtests", config);
}

export function startWalkForward(body: Record<string, unknown>): Promise<{ id: string }> {
  return postJson("/api/backtests", body);
}

export function startHoldout(fromRun: string): Promise<{ id: string }> {
  return postJson("/api/backtests/holdout", { from_run: fromRun, kind: "holdout" });
}

export function fetchRuns(): Promise<{ runs: BacktestRun[] }> {
  return getJson("/api/backtests");
}

export function fetchRun(id: string): Promise<BacktestRun> {
  return getJson(`/api/backtests/${id}`);
}
