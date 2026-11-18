import { getJson } from "./client";

/** GET /api/config/charts: instrument folder names / keys, so the UI never hard-codes them. */
export interface ChartsConfig {
  nifty_symbol: string;
  nifty_index_key?: string;
  sensex_symbol: string;
  sensex_index_key: string;
  /** false when the backend does not stream Sensex live bars */
  sensex_live_enabled?: boolean;
}

/** Last resort only: used when the config endpoint is unreachable. */
export const FALLBACK_CHARTS_CONFIG: ChartsConfig = {
  nifty_symbol: "NIFTY50",
  sensex_symbol: "SENSEX",
  sensex_index_key: "BSE_INDEX|SENSEX",
  sensex_live_enabled: true,
};

export function fetchChartsConfig(signal?: AbortSignal): Promise<ChartsConfig> {
  return getJson<ChartsConfig>("/api/config/charts", signal);
}

// ---------------------------------------------------------------- Sensex market view

export interface SensexLevel {
  key: string;
  label: string;
  price: number;
}

export interface SensexStraddle {
  atm: number;
  call_ltp: number;
  put_ltp: number;
  straddle: number;
  expected_move: number;
  range: [number, number];
}

/** GET /api/market-view/sensex (prior-day CPR / pivots + ATM straddle expected move). */
export interface SensexMarketView {
  day: string;
  built_at: number;
  symbol: string;
  factors: { key: string; label: string; score: number | null; available: boolean; value: string; reason: string }[];
  score: number;
  bias: string;
  expected_range: [number, number] | null;
  expected_move: SensexStraddle | null;
  day_type: "trend" | "range" | "mixed";
  day_type_reason: string;
  levels: SensexLevel[];
  plan: string;
}

export function fetchSensexMarketView(refresh = false, signal?: AbortSignal): Promise<SensexMarketView> {
  return getJson<SensexMarketView>(`/api/market-view/sensex${refresh ? "?refresh=true" : ""}`, signal);
}
