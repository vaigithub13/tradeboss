import { getJson, postJson, putJson, type SessionType, type Timeframe } from "../api/client";
import type { Drawing } from "./model";

export interface DrawingsPayload {
  symbol: string;
  drawings: Drawing[];
}

export function fetchDrawings(symbol: string, signal?: AbortSignal): Promise<DrawingsPayload> {
  return getJson<DrawingsPayload>(`/api/drawings?symbol=${encodeURIComponent(symbol)}`, signal);
}

export function saveDrawings(symbol: string, drawings: readonly Drawing[]): Promise<DrawingsPayload> {
  return putJson<DrawingsPayload>("/api/drawings", { symbol, drawings });
}

export function importDrawings(payload: DrawingsPayload): Promise<DrawingsPayload> {
  return postJson<DrawingsPayload>("/api/drawings/import", payload);
}

export interface FvgBox {
  direction: "bull" | "bear";
  start_time: number;
  formed_time: number;
  end_time: number | null;
  bottom: number;
  top: number;
  extends: boolean;
  faded: boolean;
  mitigated: boolean;
}

export interface FvgQuery {
  symbol: string;
  timeframe: Timeframe;
  sessions: readonly SessionType[];
  cursor: number | null;
  chartLast: number | null;
  from: number;
  to: number;
  params: Record<string, string | number>;
}

export function fetchFvg(q: FvgQuery, signal?: AbortSignal): Promise<{ boxes: FvgBox[] }> {
  return postJson<{ boxes: FvgBox[] }>(
    "/api/fvg",
    {
      symbol: q.symbol,
      timeframe: q.timeframe,
      sessions: [...q.sessions],
      cursor: q.cursor,
      chart_last: q.chartLast,
      from: q.from,
      to: q.to,
      params: q.params,
    },
    signal,
  );
}
