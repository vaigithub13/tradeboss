import { postJson } from "./client";
import type { PineCard } from "../pine/present";

export interface PineScan {
  kind: "strategy" | "indicator";
  traps: {
    session: { status: string; timeframes: Record<string, string> };
    stop_na: { status: string };
    pivot: { status: string; delay: number | null };
    lookahead: { status: string };
    overnight: { status: string };
    true_range: { calls: string[] };
  };
}

export function scanPine(source: string): Promise<{ scan: PineScan }> {
  return postJson("/api/pine/scan", { source });
}

export function reportPine(source: string): Promise<{ scan: PineScan; warnings: string[]; card: PineCard }> {
  return postJson("/api/pine/report", { source, accepted: false });
}

export function convertPine(source: string): Promise<{ python: string; tests: string }> {
  return postJson("/api/pine/convert", { source, accepted: true });
}
