import { getJson } from "./client";

export interface MarketFactor {
  key: "global" | "fii" | "crude" | "vix" | "structure";
  label: string;
  /** -1 bearish, 0 neutral, +1 bullish; null = data unavailable */
  score: number | null;
  available: boolean;
  value: string;
  reason: string;
}

export interface MarketLevel {
  key: string;
  label: string;
  price: number;
}

export interface MarketView {
  day: string;
  built_at: number;
  factors: MarketFactor[];
  score: number;
  bias: string;
  expected_range: [number, number] | null;
  day_type: "trend" | "range" | "mixed";
  day_type_reason: string;
  levels: MarketLevel[];
  plan: string;
  outcome?: {
    move: number;
    actual_type: string;
    bias_hit: boolean | null;
    range_held: boolean | null;
    day_type_hit: boolean | null;
  };
}

export interface MarketViewHistory {
  bias_hit: { n: number; rate: number | null };
  range_held: { n: number; rate: number | null };
  day_type_hit: { n: number; rate: number | null };
}

export function fetchMarketView(refresh = false, signal?: AbortSignal): Promise<MarketView> {
  return getJson<MarketView>(`/api/market-view${refresh ? "?refresh=true" : ""}`, signal);
}

export function fetchMarketViewHistory(signal?: AbortSignal): Promise<MarketViewHistory> {
  return getJson<MarketViewHistory>("/api/market-view/history", signal);
}
