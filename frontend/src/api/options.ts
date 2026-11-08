import { ApiError, getJson } from "./client";

export type OptionsStatus = "LIVE" | "MARKET_CLOSED";

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
}

export type OptionsStrength = "Strong" | "Medium" | "Weak";

export interface OptionsLevel {
  strike: number;
  oi: number;
  tag: string;
  strength?: OptionsStrength;
  oi_pct?: number;
  /** true when the level sits on the first/last strike of the ±15 window */
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

export interface OptionsRow {
  strike: number;
  call: OptionsLeg;
  put: OptionsLeg;
  atm: boolean;
  tag?: string | null;
  strength?: OptionsStrength | null;
  edge?: boolean;
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
  major_walls?: OptionsMajorWalls;
  rows: OptionsRow[];
  max_oi?: number;
}

export function fetchOptionsChain(signal?: AbortSignal, expiry?: string): Promise<OptionsChainResponse> {
  const q = expiry ? `?expiry=${encodeURIComponent(expiry)}` : "";
  return getJson<OptionsChainResponse>(`/api/options/chain${q}`, signal);
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
