import { postJson, type SessionType, type Timeframe } from "./client";

export interface IndicatorRequestItem {
  id: string;
  type: string;
  params: Record<string, number | string>;
}

export interface IndicatorsRequest {
  symbol: string;
  timeframe: Timeframe;
  /** unix seconds, inclusive */
  from?: number;
  /** unix seconds, inclusive */
  to?: number;
  sessions?: readonly SessionType[];
  indicators: IndicatorRequestItem[];
  /** replay: no returned time is after this unix time */
  cursor?: number;
}

export interface IndicatorResult {
  id: string;
  type: string;
  /** normalised by the backend (defaults filled in) */
  params: Record<string, number | string>;
  /** one array per output, aligned to `times`; null = no value (warming up / undefined) */
  outputs: Record<string, (number | null)[]>;
}

export interface IndicatorsResponse {
  symbol: string;
  timeframe: Timeframe;
  sessions: SessionType[];
  times: number[];
  indicators: IndicatorResult[];
}

export function fetchIndicators(req: IndicatorsRequest, signal?: AbortSignal): Promise<IndicatorsResponse> {
  return postJson<IndicatorsResponse>("/api/indicators", req, signal);
}
