/** Taylor estimate used by the Strike Picker (mirrors backend chain_phase3.taylor_price). */

import type { HoldMode, StrikeCandidate, StrikePickerSide } from "../api/options";

/** ΔP ≈ δ·ΔS + ½γ·(ΔS)² + θ·t_hold; floored at 0. θ is premium/day (Upstox). */
export function taylorPrice(
  ltp: number,
  delta: number,
  gamma: number,
  move: number,
  theta = 0,
  tHold = 0,
): number {
  return Math.max(0, ltp + delta * move + 0.5 * gamma * move * move + theta * tHold);
}

export function repriceCandidate(
  c: StrikeCandidate,
  spot: number,
  target: number | null,
  stop: number | null,
  tHold: number,
): StrikeCandidate {
  return {
    ...c,
    est_at_target:
      target == null ? null : taylorPrice(c.ltp, c.delta, c.gamma, target - spot, c.theta, tHold),
    est_at_stop:
      stop == null ? null : taylorPrice(c.ltp, c.delta, c.gamma, stop - spot, c.theta, tHold),
  };
}

export function repricePickerSide(
  side: StrikePickerSide,
  spot: number,
  tHold: number,
  holdMode: HoldMode,
): StrikePickerSide {
  return {
    ...side,
    t_hold: tHold,
    hold_mode: holdMode,
    candidates: side.candidates.map((c) =>
      repriceCandidate(c, spot, side.target, side.stop, tHold),
    ),
  };
}

export function formatProb(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return "—";
  return `${(p * 100).toFixed(0)}%`;
}
