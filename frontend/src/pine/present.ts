/** Pure helpers for the Pine panel. The editor and the requests live in the panel. */

export interface PineCard {
  warnings: string[];
  option_fill: string;
  overnight_net: number;
  same_day_net: number;
  checks: string[];
}

export function convertEnabled(reportAccepted: boolean): boolean {
  return reportAccepted;
}

/** Approve is offered only after Convert's check passed. The same check runs again on Approve. */
export function approveEnabled(ready: boolean): boolean {
  return ready;
}

export type PineButton = "report" | "accept" | "convert" | "approve" | "runLive";

/** Why a Pine panel button is disabled. Empty when the button can be used. */
export function disabledReasons(
  button: PineButton,
  state: {
    busy: boolean;
    hasReport: boolean;
    accepted: boolean;
    ready: boolean;
    approved: boolean;
    errors: string[];
    scriptCount?: number;
    unsupported?: string[];
  },
): string[] {
  if (button === "report") {
    return state.busy ? ["The semantics report is still running."] : [];
  }
  if (button === "accept") {
    if (state.busy) return ["Accept is still running."];
    if (!state.hasReport) return ["Run the semantics report before accepting."];
    return [];
  }
  if (button === "convert") {
    if (state.busy) return ["Convert is still running."];
    if (!state.accepted) return ["Accept the semantics report before converting."];
    return [];
  }
  if (button === "runLive") {
    if (state.busy) return ["Busy."];
    if (!state.accepted) return ["Accept the semantics report before running live."];
    if ((state.unsupported?.length ?? 0) > 0) return state.unsupported ?? [];
    if ((state.scriptCount ?? 0) >= 5) return ["Max 5 live scripts."];
    return [];
  }
  if (state.approved) return ["This diff is already saved."];
  if (state.busy) return ["The check is still running."];
  if (!state.ready) return state.errors.length > 0 ? state.errors : ["The draft failed the checks."];
  return [];
}

export function runLiveEnabled(accepted: boolean, unsupportedCount: number, scriptCount: number): boolean {
  return accepted && unsupportedCount === 0 && scriptCount < 5;
}

/** Script title from `strategy("…")` / `indicator("…")`, else a fallback. */
export function scriptTitle(source: string, fallback = "Pine script"): string {
  const m = source.match(/\b(?:strategy|indicator)\s*\(\s*["']([^"']+)["']/);
  return m?.[1] ?? fallback;
}

/** A second Approve of a saved name asks before replacing the file. */
export function replaceAsked(status: number): boolean {
  return status === 409;
}

export function showBacktestCard(kind: "strategy" | "indicator"): boolean {
  return kind === "strategy";
}

export function cardLines(card: PineCard): string[] {
  return [
    ...card.warnings,
    `fill ${card.option_fill}`,
    `overnight ${card.overnight_net} same-day ${card.same_day_net}`,
  ];
}
