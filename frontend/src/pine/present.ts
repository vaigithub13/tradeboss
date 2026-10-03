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
