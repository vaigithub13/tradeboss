import { getJson } from "./client";

/** One strike of GET /api/gex. Values are ₹ crore of hedging per 1% move; put is ≤ 0. */
export interface GexStrike {
  strike: number;
  call: number;
  put: number;
  net: number;
}

export interface GexResponse {
  available: boolean;
  reason?: string | null;
  expiry: string;
  /** unix seconds the chain was read */
  as_of: number;
  unit: string;
  spot?: number;
  strikes: GexStrike[];
  total_call?: number;
  total_put?: number;
  total_net?: number;
  regime?: "positive" | "negative";
  call_wall?: number | null;
  put_wall?: number | null;
  gamma_flip?: number | null;
}

export function fetchGex(signal?: AbortSignal, expiry?: string): Promise<GexResponse> {
  const q = expiry ? `?expiry=${encodeURIComponent(expiry)}` : "";
  return getJson<GexResponse>(`/api/gex${q}`, signal);
}
