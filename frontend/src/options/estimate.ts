/** Taylor estimate + strike-picker scoring (mirrors backend chain_phase3). */

import type {
  HoldMode,
  StrikeCandidate,
  StrikeCheck,
  StrikePickerSide,
} from "../api/options";

const SPREAD_PASS_PCT = 2.0;
const SPREAD_WARN_PCT = 3.0;
const RR_PASS = 2.0;
const RR_WARN = 1.5;
const THETA_DRAG_PASS = 0.1;
const THETA_DRAG_WARN = 0.15;
export const NO_SETUP_MSG = "No good buy setup now";

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

function checkStatus(
  value: number | null,
  passAt: number,
  warnAt: number,
  higherIsBetter: boolean,
): StrikeCheck["status"] {
  if (value == null || Number.isNaN(value)) return "fail";
  if (higherIsBetter) {
    if (value >= passAt) return "pass";
    if (value >= warnAt) return "warn";
    return "fail";
  }
  if (value <= passAt) return "pass";
  if (value <= warnAt) return "warn";
  return "fail";
}

export function scoreCandidate(c: StrikeCandidate, tHold: number): StrikeCandidate {
  const ltp = c.ltp;
  const estGain = c.est_at_target != null ? c.est_at_target - ltp : null;
  const estLoss = c.est_at_stop != null ? ltp - c.est_at_stop : null;
  const rr = estGain != null && estLoss != null && estLoss > 0 ? estGain / estLoss : null;
  const drag = ltp > 0 ? (Math.abs(c.theta) * tHold) / ltp : null;
  const checks: StrikeCheck[] = [
    {
      id: "spread",
      label: "Spread",
      status:
        c.spread_pct == null
          ? "fail"
          : checkStatus(c.spread_pct, SPREAD_PASS_PCT, SPREAD_WARN_PCT, false),
      detail: c.spread_pct == null ? null : `${c.spread_pct.toFixed(2)}%`,
    },
    {
      id: "rr",
      label: "R:R",
      status: checkStatus(rr, RR_PASS, RR_WARN, true),
      detail: rr == null ? null : rr.toFixed(2),
    },
    {
      id: "loss",
      label: "Est. loss",
      status: estLoss != null && estLoss > 0 ? "pass" : "fail",
      detail: estLoss == null ? null : estLoss.toFixed(2),
    },
    {
      id: "theta_drag",
      label: "θ drag",
      status:
        drag == null ? "fail" : checkStatus(drag, THETA_DRAG_PASS, THETA_DRAG_WARN, false),
      detail: drag == null ? null : `${(drag * 100).toFixed(1)}%`,
    },
  ];
  const failed = checks.filter((ch) => ch.status === "fail").map((ch) => ch.label);
  return {
    ...c,
    est_gain: estGain,
    est_loss: estLoss,
    reward_risk: rr,
    checks,
    passed: failed.length === 0,
    failed_checks: failed,
  };
}

export function applyPickerHighlight(candidates: StrikeCandidate[]): {
  candidates: StrikeCandidate[];
  highlight: string | null;
  closest: string | null;
  closest_fails: string[];
  setup_message: string | null;
  has_setup: boolean;
} {
  const scored = candidates.map((c) => (c.checks ? c : scoreCandidate(c, 0)));
  const passing = scored.filter((c) => c.passed);
  const rrKey = (c: StrikeCandidate) => c.reward_risk ?? Number.NEGATIVE_INFINITY;

  if (passing.length) {
    const best = [...passing].sort((a, b) => rrKey(b) - rrKey(a))[0]!;
    const highlight = best.bucket;
    return {
      candidates: scored.map((c) => ({
        ...c,
        highlighted: c.bucket === highlight,
        closest_failed: false,
      })),
      highlight,
      closest: null,
      closest_fails: [],
      setup_message: null,
      has_setup: true,
    };
  }

  if (!scored.length) {
    return {
      candidates: scored,
      highlight: null,
      closest: null,
      closest_fails: [],
      setup_message: NO_SETUP_MSG,
      has_setup: false,
    };
  }
  const closestC = [...scored].sort((a, b) => rrKey(b) - rrKey(a))[0]!;
  return {
    candidates: scored.map((c) => ({
      ...c,
      highlighted: false,
      closest_failed: c.bucket === closestC.bucket,
    })),
    highlight: null,
    closest: closestC.bucket,
    closest_fails: closestC.failed_checks ?? [],
    setup_message: NO_SETUP_MSG,
    has_setup: false,
  };
}

export function repriceCandidate(
  c: StrikeCandidate,
  spot: number,
  target: number | null,
  stop: number | null,
  tHold: number,
): StrikeCandidate {
  const priced = {
    ...c,
    est_at_target:
      target == null ? null : taylorPrice(c.ltp, c.delta, c.gamma, target - spot, c.theta, tHold),
    est_at_stop:
      stop == null ? null : taylorPrice(c.ltp, c.delta, c.gamma, stop - spot, c.theta, tHold),
  };
  return scoreCandidate(priced, tHold);
}

export function repricePickerSide(
  side: StrikePickerSide,
  spot: number,
  tHold: number,
  holdMode: HoldMode,
): StrikePickerSide {
  const scored = side.candidates.map((c) =>
    repriceCandidate(c, spot, side.target, side.stop, tHold),
  );
  const hi = applyPickerHighlight(scored);
  return {
    ...side,
    t_hold: tHold,
    hold_mode: holdMode,
    candidates: hi.candidates,
    highlight: hi.highlight,
    closest: hi.closest,
    closest_fails: hi.closest_fails,
    setup_message: hi.setup_message,
    has_setup: hi.has_setup,
  };
}

export function formatProb(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return "—";
  return `${(p * 100).toFixed(0)}%`;
}

export function checkGlyph(status: StrikeCheck["status"]): string {
  if (status === "pass") return "✅";
  if (status === "warn") return "⚠️";
  return "❌";
}

/** Next expiry after `current` in a sorted ISO date list. */
export function nextExpiryAfter(current: string, expiries: string[]): string | null {
  const sorted = [...expiries].sort();
  const i = sorted.indexOf(current);
  if (i >= 0 && i + 1 < sorted.length) return sorted[i + 1] ?? null;
  const later = sorted.find((e) => e > current);
  return later ?? null;
}
