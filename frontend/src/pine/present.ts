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

export type PineButton = "report" | "accept" | "convert" | "approve";

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
  if (state.approved) return ["This diff is already saved."];
  if (state.busy) return ["The check is still running."];
  if (!state.ready) return state.errors.length > 0 ? state.errors : ["The draft failed the checks."];
  return [];
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
