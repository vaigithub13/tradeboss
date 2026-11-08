import { ApiError, getJson } from "./client";

export type OptionsStatus = "LIVE" | "MARKET_CLOSED";
export type BuildupLabel =
  | "Long Build-up"
  | "Short Build-up"
  | "Short Covering"
  | "Long Unwinding"
  | "Neutral";
export type NiftyBias = "Bullish" | "Bearish";
export type PcrRead = "Bullish" | "Neutral" | "Bearish";

export interface OptionsLeg {
  oi: number;
  prev_oi: number;
  oi_chg: number;
  oi_chg_pct: number | null;
  volume: number;
  iv: number | null;
  ltp: number;
  close: number;
  price_chg: number;
  bid: number;
  ask: number;
  spread_pct: number | null;
  buildup?: BuildupLabel;
  buildup_nifty?: NiftyBias | null;
}

export type OptionsStrength = "Strong" | "Medium" | "Weak";

export interface OptionsLevel {
  strike: number;
  oi: number;
  tag: string;
  strength?: OptionsStrength;
  oi_pct?: number;
  edge?: boolean;
}

export interface OptionsWall {
  strike: number;
  oi: number;
}

export interface OptionsMajorWalls {
  call: OptionsWall | null;
  put: OptionsWall | null;
}

export interface OptionsPivot {
  strike: number;
  call_oi: number;
  put_oi: number;
  heavy_call: boolean;
  heavy_put: boolean;
  tag: "PIVOT";
  read: string;
}

export interface OptionsMaxPain {
  strike: number;
  pain?: number;
  distance?: number;
  tag: string;
}

export interface PcrValue {
  value: number | null;
  display: number | string;
  reason?: string | null;
  read?: PcrRead | null;
}

export interface OptionsPcrs {
  full: PcrValue;
  near_atm: PcrValue;
  change_oi: PcrValue;
}

export interface BuildupAnalysis {
  zone_strikes: number[];
  call_doi: number;
  put_doi: number;
  top_call_additions: { strike: number; oi_chg: number }[];
  top_put_additions: { strike: number; oi_chg: number }[];
  summary: string;
  bias: "Bullish" | "Bearish" | "Mixed";
}

export interface OptionsRow {
  strike: number;
  call: OptionsLeg;
  put: OptionsLeg;
  atm: boolean;
  tag?: string | null;
  strength?: OptionsStrength | null;
  edge?: boolean;
  max_pain?: boolean;
  call_itm: boolean;
  put_itm: boolean;
}

export interface OptionsChainResponse {
  available: boolean;
  reason?: string | null;
  expiry: string;
  expiries?: string[];
  as_of: number;
  as_of_ist: string;
  status: OptionsStatus;
  from_snapshot?: boolean;
  spot?: number;
  atm?: number;
  pcr?: number | null;
  total_call_oi?: number;
  total_put_oi?: number;
  resistance?: OptionsLevel[];
  support?: OptionsLevel[];
  pivot?: OptionsPivot | null;
  max_pain?: OptionsMaxPain | null;
  major_walls?: OptionsMajorWalls;
  pcrs?: OptionsPcrs;
  buildup_analysis?: BuildupAnalysis;
  near_atm_strikes?: number[];
  rows: OptionsRow[];
  max_oi?: number;
  max_oi_chg?: number;
}

export interface OptionsTrendPoint {
  t: number;
  as_of_ist?: string;
  spot?: number | null;
  pcr_full?: number | null;
  pcr_near_atm?: number | null;
  expiry?: string;
}

export interface OptionsTrendsResponse {
  day: string;
  available: boolean;
  message?: string | null;
  points: OptionsTrendPoint[];
}

export function fetchOptionsChain(signal?: AbortSignal, expiry?: string): Promise<OptionsChainResponse> {
  const q = expiry ? `?expiry=${encodeURIComponent(expiry)}` : "";
  return getJson<OptionsChainResponse>(`/api/options/chain${q}`, signal);
}

export function fetchOptionsTrends(signal?: AbortSignal, expiry?: string): Promise<OptionsTrendsResponse> {
  const q = expiry ? `?expiry=${encodeURIComponent(expiry)}` : "";
  return getJson<OptionsTrendsResponse>(`/api/options/trends${q}`, signal);
}

export function optionsErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 503) return err.message || "Data token missing or expired";
    if (err.status === 429) return "Upstox rate limit — try again in a moment";
    if (err.status === 502) return err.message || "Option chain request failed";
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}
