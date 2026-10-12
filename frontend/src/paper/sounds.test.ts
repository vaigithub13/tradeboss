import { describe, expect, it } from "vitest";

import { ENTRY_LENGTH, entryPlan, exitPlan, planLength } from "./sounds";

describe("paper alert sounds", () => {
  it("entry: a rising chime of about 2 s, played twice", () => {
    const plan = entryPlan();
    expect(ENTRY_LENGTH).toBeGreaterThan(1.8);
    expect(ENTRY_LENGTH).toBeLessThan(2.3);
    const firstPass = plan.filter((t) => t.at < ENTRY_LENGTH && t.type === "triangle");
    const secondPass = plan.filter((t) => t.at >= ENTRY_LENGTH && t.type === "triangle");
    expect(firstPass.map((t) => t.freq)).toEqual(secondPass.map((t) => t.freq));
    const freqs = firstPass.map((t) => t.freq);
    expect(freqs).toEqual([...freqs].sort((a, b) => a - b)); // rising
    expect(planLength(plan)).toBeGreaterThan(2 * 1.8);
  });

  it("exit: three low bell strikes, nothing like the entry", () => {
    const plan = exitPlan();
    const strikes = [...new Set(plan.map((t) => t.at))];
    expect(strikes).toHaveLength(3);
    const fundamentals = strikes.map((at) => Math.min(...plan.filter((t) => t.at === at).map((t) => t.freq)));
    expect(Math.max(...fundamentals)).toBeLessThan(Math.min(...entryPlan().map((t) => t.freq)) / 2); // well below
    // inharmonic partials: a bell, not a note
    const f0 = fundamentals[0] ?? 1;
    const ratios = plan.filter((t) => t.at === 0).map((t) => t.freq / f0);
    expect(ratios.some((r) => Math.abs(r - Math.round(r)) > 0.2)).toBe(true);
  });
});
