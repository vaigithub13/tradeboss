/** Messages of the backend WebSocket /api/live/ws (see backend/app/live/hub.py). */
import type { Candle } from "../api/client";

export type LiveState = "live" | "stale" | "reconnecting" | "closed" | "auth_failed" | "locked" | "disabled";

export interface LiveStatus {
  state: LiveState;
  since_last_tick_s: number | null;
  market: "open" | "closed" | "unknown";
  connection?: string;
  recording?: boolean;
  withheld?: string[];
  unreconciled?: [string, string][];
}

export interface StatusMessage extends LiveStatus {
  type: "status";
}

export interface BarMessage {
  type: "bar";
  symbol: string;
  timeframe: string;
  /** the newest (up to) 2 candles of the timeframe */
  candles: Candle[];
  /** false right after a reconnect, until the exchange's own 1-minute bar arrives */
  volume_known: boolean;
  /** candle start times the indicator values below belong to */
  times?: number[];
  indicators?: { id: string; outputs: Record<string, (number | null)[]> }[];
}

export interface ReloadMessage {
  type: "reload";
  symbol: string;
}

export interface ErrorMessage {
  type: "error";
  message: string;
  symbol?: string;
}

export type ServerMessage = StatusMessage | BarMessage | ReloadMessage | ErrorMessage | { type: "pong" };

/** What the tab is looking at: sent to the server so it knows which tail to compute. */
export interface LiveView {
  symbol: string;
  timeframe: string;
  sessions: string[];
  indicators: { id: string; type: string; params: Record<string, unknown> }[];
}
