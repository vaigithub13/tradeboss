import { describe, expect, it } from "vitest";

import {
  applyPickerHighlight,
  formatProb,
  nextExpiryAfter,
  NO_SETUP_MSG,
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
    expect(s.checks?.length).toBe(4);
    expect(s.passed).toBe(false); // R:R fails
  });

  it("does not highlight when nothing passes", () => {
    const a = scoreCandidate({ ...base, bucket: "ITM", est_at_target: 105, est_at_stop: 20 }, 1);
    const b = scoreCandidate({ ...base, bucket: "ATM", est_at_target: 108, est_at_stop: 30 }, 1);
    const hi = applyPickerHighlight([a, b]);
    expect(hi.has_setup).toBe(false);
    expect(hi.highlight).toBeNull();
    expect(hi.setup_message).toBe(NO_SETUP_MSG);
    expect(hi.closest).toBeTruthy();
    expect(hi.candidates.some((c) => c.closest_failed)).toBe(true);
    expect(hi.candidates.every((c) => !c.highlighted)).toBe(true);
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
