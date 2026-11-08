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
const LOSS_PASS_PCT = 0.4;
const LOSS_WARN_PCT = 0.7;
export const NO_SETUP_MSG = "No good buy setup now";
export const NO_EDGE_MSG =
  "No edge without a directional signal — R:R alone isn't enough";
export const DEFAULT_ROUND_TRIP_CHARGES = 60;
export const BIAS_WIN_BUMP_AT_10 = 0.12;
export const BIAS_CONFIDENCE_WARNING = "UNCALIBRATED — validate in Phase 5 backtest";

/** Fractional win-chance bump: 0 at score 0 → +0.12 at |10|. */
export function biasWinBump(score: number): number {
  return (BIAS_WIN_BUMP_AT_10 * Math.min(10, Math.abs(score))) / 10;
}

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

/** P(hit target before stop) ≈ stop_dist / (stop_dist + target_dist). */
export function driftlessWinChance(
  stopDist: number | null | undefined,
  targetDist: number | null | undefined,
): number | null {
  if (stopDist == null || targetDist == null) return null;
  if (stopDist <= 0 || targetDist <= 0) return null;
  return stopDist / (stopDist + targetDist);
}

/** Win rate for zero EV before charges: 1 / (1 + R:R). */
export function breakevenWinPct(rr: number | null | undefined): number | null {
  if (rr == null || Number.isNaN(rr) || rr < 0) return null;
  return 1 / (1 + rr);
}

/** (win×gain − (1−win)×loss) × lot − charges, in ₹. */
export function evPerLot(
  win: number,
  gain: number,
  loss: number,
  lotSize: number,
  charges = DEFAULT_ROUND_TRIP_CHARGES,
): number {
  return (win * gain - (1 - win) * loss) * lotSize - charges;
}

export function scoreCandidate(c: StrikeCandidate, tHold: number): StrikeCandidate {
  const ltp = c.ltp;
  const estGain = c.est_at_target != null ? c.est_at_target - ltp : null;
  const estLoss = c.est_at_stop != null ? ltp - c.est_at_stop : null;
  const rr = estGain != null && estLoss != null && estLoss > 0 ? estGain / estLoss : null;
  const drag = ltp > 0 ? (Math.abs(c.theta) * tHold) / ltp : null;
  const lossPct = estLoss != null && ltp > 0 ? estLoss / ltp : null;
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
      status:
        lossPct != null && estLoss != null && estLoss > 0
          ? checkStatus(lossPct, LOSS_PASS_PCT, LOSS_WARN_PCT, false)
          : "fail",
      detail: estLoss == null ? null : `-${estLoss.toFixed(2)}`,
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
    breakeven_win: breakevenWinPct(rr),
    checks,
    passed: failed.length === 0,
    failed_checks: failed,
  };
}

export function attachEvMetrics(
  candidates: StrikeCandidate[],
  opts: {
    stopDist: number | null | undefined;
    targetDist: number | null | undefined;
    lotSize: number | null | undefined;
    charges?: number;
    win?: number | null;
  },
): { candidates: StrikeCandidate[]; driftless: number | null } {
  const driftless = driftlessWinChance(opts.stopDist, opts.targetDist);
  const useWin = opts.win != null ? opts.win : driftless;
  const charges = opts.charges ?? DEFAULT_ROUND_TRIP_CHARGES;
  const lot = opts.lotSize != null ? opts.lotSize : null;
  const out = candidates.map((c) => {
    let ev: number | null = null;
    if (
      useWin != null &&
      c.est_gain != null &&
      c.est_loss != null &&
      c.est_loss > 0 &&
      lot != null &&
      lot > 0
    ) {
      ev = evPerLot(useWin, c.est_gain, c.est_loss, lot, charges);
    }
    return {
      ...c,
      win_chance: useWin,
      win_chance_driftless: driftless,
      breakeven_win: c.breakeven_win ?? breakevenWinPct(c.reward_risk),
      ev_per_lot: ev,
      round_trip_charges: charges,
    };
  });
  return { candidates: out, driftless };
}

export function applyPickerHighlight(candidates: StrikeCandidate[]): {
  candidates: StrikeCandidate[];
  highlight: string | null;
  closest: string | null;
  closest_fails: string[];
  setup_message: string | null;
  has_setup: boolean;
} {
  const scored = [...candidates].sort((a, b) => {
    const evA = a.ev_per_lot ?? Number.NEGATIVE_INFINITY;
    const evB = b.ev_per_lot ?? Number.NEGATIVE_INFINITY;
    if (evB !== evA) return evB - evA;
    return (b.reward_risk ?? Number.NEGATIVE_INFINITY) - (a.reward_risk ?? Number.NEGATIVE_INFINITY);
  });
  const evKey = (c: StrikeCandidate) => c.ev_per_lot ?? Number.NEGATIVE_INFINITY;
  const eligible = scored.filter(
    (c) => c.passed && c.ev_per_lot != null && c.ev_per_lot > 0,
  );

  if (eligible.length) {
    const best = [...eligible].sort((a, b) => evKey(b) - evKey(a))[0]!;
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

  const evs = scored
    .map((c) => c.ev_per_lot)
    .filter((ev): ev is number => ev != null && !Number.isNaN(ev));
  const message =
    scored.length === 0
      ? NO_SETUP_MSG
      : evs.length && evs.every((ev) => ev <= 0)
        ? NO_EDGE_MSG
        : scored.some((c) => c.passed)
          ? NO_EDGE_MSG
          : NO_SETUP_MSG;

  if (!scored.length) {
    return {
      candidates: scored,
      highlight: null,
      closest: null,
      closest_fails: [],
      setup_message: message,
      has_setup: false,
    };
  }
  const closestC = [...scored].sort((a, b) => evKey(b) - evKey(a))[0]!;
  return {
    candidates: scored.map((c) => ({
      ...c,
      highlighted: false,
      closest_failed: c.bucket === closestC.bucket,
    })),
    highlight: null,
    closest: closestC.bucket,
    closest_fails: closestC.failed_checks ?? [],
    setup_message: message,
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
  opts?: {
    signalWin?: number | null;
    charges?: number;
  },
): StrikePickerSide {
  const scored = side.candidates.map((c) =>
    repriceCandidate(c, spot, side.target, side.stop, tHold),
  );
  const stopDist =
    side.stop_distance ?? (side.stop != null ? Math.abs(spot - side.stop) : null);
  const targetDist =
    side.target_distance ?? (side.target != null ? Math.abs(side.target - spot) : null);
  const charges = opts?.charges ?? side.round_trip_charges ?? DEFAULT_ROUND_TRIP_CHARGES;
  const { candidates: withEv, driftless } = attachEvMetrics(scored, {
    stopDist,
    targetDist,
    lotSize: side.lot_size,
    charges,
    win: opts?.signalWin ?? null,
  });
  const hi = applyPickerHighlight(withEv);
  // Auto "needs move" keeps no-setup even if fallback target scores pass after reprice
  const blocked = Boolean(side.auto_message);
  return {
    ...side,
    t_hold: tHold,
    hold_mode: holdMode,
    target_distance: targetDist,
    stop_distance: stopDist,
    win_chance: opts?.signalWin ?? driftless,
    win_chance_driftless: driftless,
    round_trip_charges: charges,
    candidates: blocked
      ? hi.candidates.map((c) => ({ ...c, highlighted: false, closest_failed: c.closest_failed }))
      : hi.candidates,
    highlight: blocked ? null : hi.highlight,
    closest: hi.closest,
    closest_fails: hi.closest_fails,
    setup_message: blocked ? side.setup_message ?? NO_SETUP_MSG : hi.setup_message,
    has_setup: blocked ? false : hi.has_setup,
  };
}

export function formatProb(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return "—";
  return `${(p * 100).toFixed(0)}%`;
}

export function formatInr(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `₹${sign}${Math.abs(n).toFixed(0)}`;
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
