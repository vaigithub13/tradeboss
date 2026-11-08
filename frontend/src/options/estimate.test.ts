import { describe, expect, it } from "vitest";

import { formatProb, taylorPrice } from "./estimate";

describe("taylorPrice", () => {
  it("includes theta · t_hold", () => {
    // 100 + 0.5*100 + 0.5*0.002*10000 + (−10)*1 = 100+50+10−10 = 150
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
