import { describe, expect, it } from "vitest";

import {
  applyPickerHighlight,
  attachEvMetrics,
  biasWinBump,
  breakevenWinPct,
  driftlessWinChance,
  evPerLot,
  formatProb,
  nextExpiryAfter,
  NO_EDGE_MSG,
  scoreCandidate,
  taylorPrice,
} from "./estimate";
import type { StrikeCandidate } from "../api/options";

describe("taylorPrice", () => {
  it("includes theta · t_hold", () => {
    expect(taylorPrice(100, 0.5, 0.002, 100, -10, 1)).toBeCloseTo(150);
  });

  it("floors at zero", () => {
    expect(taylorPrice(5, -0.5, 0, 100, -10, 1)).toBe(0);
  });
});

describe("formatProb", () => {
  it("renders percent", () => {
    expect(formatProb(0.42)).toBe("42%");
    expect(formatProb(null)).toBe("—");
  });
});

describe("win / breakeven / EV (Friday fixture)", () => {
  it("matches Friday Auto R2 distances", () => {
    const stopDist = 91;
    const targetDist = 25300 - 25030.35;
    const win = driftlessWinChance(stopDist, targetDist);
    expect(win).toBeCloseTo(stopDist / (stopDist + targetDist));
    expect(win).toBeCloseTo(0.2523, 3);

    const rr = 2.54;
    expect(breakevenWinPct(rr)).toBeCloseTo(1 / (1 + rr));

    const gain = 192.34;
    const loss = 75.6;
    const ev = evPerLot(win!, gain, loss, 65, 60);
    expect(ev).toBeCloseTo((win! * gain - (1 - win!) * loss) * 65 - 60);
    expect(ev).toBeLessThan(0);
    expect(evPerLot(0.45, gain, loss, 65, 60)).toBeGreaterThan(0);
  });
});

describe("scoreCandidate + highlight", () => {
  const base: StrikeCandidate = {
    bucket: "ATM",
    strike: 25000,
    side: "call",
    ltp: 100,
    delta: 0.5,
    gamma: 0.002,
    theta: -5,
    theta_pct_premium: 5,
    iv: 15,
    spread_pct: 0.5,
    lot_size: 65,
    cost_per_lot: 6500,
    est_at_target: 110,
    est_at_stop: 40,
  };

  it("computes gain loss R:R and checks", () => {
    const s = scoreCandidate(base, 1);
    expect(s.est_gain).toBeCloseTo(10);
    expect(s.est_loss).toBeCloseTo(60);
    expect(s.reward_risk).toBeCloseTo(10 / 60);
    expect(s.breakeven_win).toBeCloseTo(1 / (1 + 10 / 60));
    expect(s.checks?.length).toBe(4);
    expect(s.passed).toBe(false); // R:R fails
  });

  it("does not highlight when nothing passes", () => {
    const a = scoreCandidate({ ...base, bucket: "ITM", est_at_target: 105, est_at_stop: 20 }, 1);
    const b = scoreCandidate({ ...base, bucket: "ATM", est_at_target: 108, est_at_stop: 30 }, 1);
    const { candidates } = attachEvMetrics([a, b], {
      stopDist: 100,
      targetDist: 200,
      lotSize: 65,
      charges: 60,
    });
    const hi = applyPickerHighlight(candidates);
    expect(hi.has_setup).toBe(false);
    expect(hi.highlight).toBeNull();
    expect(hi.setup_message).toBe(NO_EDGE_MSG); // EV ≤ 0 under driftless
    expect(hi.closest).toBeTruthy();
    expect(hi.candidates.some((c) => c.closest_failed)).toBe(true);
    expect(hi.candidates.every((c) => !c.highlighted)).toBe(true);
  });

  it("ranks ★ by EV and shows no-edge when all EV ≤ 0", () => {
    const passy = scoreCandidate(
      {
        ...base,
        bucket: "ATM",
        theta: -1,
        est_at_target: 300,
        est_at_stop: 70,
      },
      1,
    );
    expect(passy.passed).toBe(true);
    const { candidates } = attachEvMetrics([passy], {
      stopDist: 50,
      targetDist: 500,
      lotSize: 65,
      charges: 60,
    });
    expect(candidates[0]!.ev_per_lot!).toBeLessThanOrEqual(0);
    const hi = applyPickerHighlight(candidates);
    expect(hi.has_setup).toBe(false);
    expect(hi.setup_message).toBe(NO_EDGE_MSG);
  });
});

describe("biasWinBump", () => {
  it("maps |score| to up to +12 pts", () => {
    expect(biasWinBump(0)).toBe(0);
    expect(biasWinBump(10)).toBeCloseTo(0.12);
    expect(biasWinBump(-5)).toBeCloseTo(0.06);
  });
});

describe("nextExpiryAfter", () => {
  it("picks the following listed expiry", () => {
    expect(nextExpiryAfter("2026-11-09", ["2026-11-09", "2026-11-17", "2026-11-24"])).toBe(
      "2026-11-17",
    );
    expect(nextExpiryAfter("2026-11-24", ["2026-11-09", "2026-11-17", "2026-11-24"])).toBeNull();
  });
});
