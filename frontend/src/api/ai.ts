import { getJson, postJson, type SessionType } from "./client";
import type { AutoSettings } from "../ai/auto";
import type { AnalysisView } from "../ai/present";
import { replayCursor } from "../replay/session";

export interface AnalyseResult {
  ready: boolean;
  id: string | null;
  mode: "live" | "replay";
  analysis: AnalysisView | null;
  error: string | null;
  usage: { prompt_tokens: number; completion_tokens: number };
  cost: string;
  model: string;
  attempts: number;
}

export interface TrackHorizon {
  scored: number;
  ai: { bias: number | null; triggers: number | null; levels: number | null };
  always_bullish: { bias: number | null };
  follow_trend: { bias: number | null };
}

export interface TrackResult {
  symbol: string;
  horizons: Record<string, TrackHorizon>;
}

export function analyseSymbol(
  symbol: string,
  sessions: readonly SessionType[],
  imageBase64?: string,
): Promise<AnalyseResult> {
  return postJson<AnalyseResult>("/api/ai/analyse", {
    symbol,
    sessions,
    image_base64: imageBase64 ?? null,
    cursor: replayCursor(),
  });
}

export function fetchAiSettings(): Promise<AutoSettings> {
  return getJson<AutoSettings>("/api/ai/settings");
}

export function fetchTrack(symbol: string, sessions: readonly SessionType[]): Promise<TrackResult> {
  const query = new URLSearchParams({ symbol, sessions: sessions.join(",") });
  return getJson<TrackResult>(`/api/ai/track?${query.toString()}`);
}
