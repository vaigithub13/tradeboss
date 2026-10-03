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

export interface PineReport {
  id: string;
  hash: string;
  scan: PineScan;
  warnings: string[];
  model: unknown;
  card: PineCard;
}

export function reportPine(source: string): Promise<PineReport> {
  return postJson("/api/pine/report", { source });
}

export function acceptReport(reportId: string, reportHash: string): Promise<{ accepted: boolean }> {
  return postJson("/api/pine/accept", { report_id: reportId, report_hash: reportHash });
}

export function convertPine(
  source: string,
  report: PineReport,
): Promise<{ python: string; tests: string; ready: boolean; errors: string[]; id: string; hash: string }> {
  return postJson("/api/pine/convert", {
    source,
    report_id: report.id,
    report: { scan: report.scan, model: report.model, warnings: report.warnings, card: report.card },
  });
}

export function approveDraft(draftId: string, draftHash: string): Promise<{ approved: boolean }> {
  return postJson("/api/pine/approve", { draft_id: draftId, draft_hash: draftHash });
}
