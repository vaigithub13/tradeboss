import { describe, expect, it } from "vitest";

import { formatChg, formatOiLakhs, formatSpot } from "./format";

describe("options format", () => {
  it("shows OI in lakhs", () => {
    expect(formatOiLakhs(1_234_567)).toMatch(/12\.35 L/);
    expect(formatOiLakhs(0)).toBe("0 L");
  });

  it("formats spot and signed change", () => {
    expect(formatSpot(25023.4)).toMatch(/25,023/);
    expect(formatChg(5.5)).toBe("+5.50");
    expect(formatChg(-3)).toBe("−3.00");
  });
});
