import { getJson } from "./client";

export interface ForwardStat {
  return_pct: number;
  mfe_atr: number;
  mae_atr: number;
}

export interface RateBlock {
  n: number;
  pct_up: number | null;
  median_return: number | null;
  spread: number | null;
}

export interface HorizonSummary {
  matches: RateBlock;
  base: RateBlock;
}

export interface Reliability {
  n: number;
  hit_rate: number | null;
  base_rate: number | null;
  up_rate: number | null;
  verdict: string;
  sample_size: number;
  evaluated: number;
  runtime_s: number;
  capped: boolean;
  clocks?: string[];
}

export interface CandleMatch {
  start: number;
  end: number;
  forward_end: number;
  similarity: number;
  forward: Record<string, ForwardStat | null>;
}

export interface CandleResult {
  symbol: string;
  timeframe: string;
  window: number;
  as_of: number | null;
  method: string;
  note: string;
  window_rule: string;
  query: { start: number; end: number; forward_end: number } | null;
  empty_reason?: string;
  matches: CandleMatch[];
  summary: Record<string, HorizonSummary>;
  reliability: Record<string, Reliability> | null;
}

export interface DayMatch {
  date: string;
  similarity: number;
  close_vs_open_pct: number;
  up_close: boolean;
  broke_high: boolean;
  broke_low: boolean;
  day_type: "trend" | "range";
  end_time: number;
}

export interface DayOutcome {
  label: string;
  hits: number;
  n: number;
  base_rate: number | null;
  base_n: number;
  line: string;
}

export interface DayResult {
  symbol: string;
  at: string;
  clock: string;
  as_of: number | null;
  date: string | null;
  note: string;
  window_rule: string;
  matches: DayMatch[];
  outcomes: Record<string, DayOutcome>;
  reliability: Reliability | null;
}

export const SIMILAR_TIMEFRAMES = ["5m", "15m", "1h", "1D"] as const;
export type SimilarTimeframe = (typeof SIMILAR_TIMEFRAMES)[number];
export const INDEX_SYMBOLS = ["NIFTY50", "SENSEX", "BANKNIFTY"] as const;

export function fetchSimilarCandles(
  symbol: string,
  timeframe: SimilarTimeframe,
  window: number,
  asOf: number | null,
  method: "euclidean" | "dtw" = "euclidean",
  signal?: AbortSignal,
): Promise<CandleResult> {
  const q = new URLSearchParams({ symbol, timeframe, window: String(window), method });
  if (asOf != null) q.set("as_of", String(asOf));
  return getJson<CandleResult>(`/api/similar/candles?${q}`, signal);
}

export function fetchSimilarDays(
  symbol: string,
  at: "09:30" | "10:15" | "now",
  asOf: number | null,
  signal?: AbortSignal,
): Promise<DayResult> {
  const q = new URLSearchParams({ symbol, at });
  if (asOf != null) q.set("as_of", String(asOf));
  return getJson<DayResult>(`/api/similar/days?${q}`, signal);
}
