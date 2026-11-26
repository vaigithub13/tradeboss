import { describe, expect, it } from "vitest";

import { ENTRY_LENGTH, assistEntryPlan, assistExitPlan, buyPlan, entryPlan, exitPlan, planLength, sellPlan } from "./sounds";

/** Frozen snapshot: paper entry/exit must not change when Pine buy/sell plans are added. */
const PAPER_ENTRY_FREQS = [523.25, 659.25, 783.99, 1046.5, 523.25, 659.25, 783.99, 1046.5];
const PAPER_EXIT_FUNDAMENTALS = [196, 196, 147];

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

  it("paper entry/exit plans are unchanged after Pine buy/sell were added", () => {
    const entryTri = entryPlan().filter((t) => t.type === "triangle").map((t) => t.freq);
    expect(entryTri).toEqual(PAPER_ENTRY_FREQS);
    const exitStrikes = [...new Set(exitPlan().map((t) => t.at))];
    const exitFund = exitStrikes.map((at) => Math.min(...exitPlan().filter((t) => t.at === at).map((t) => t.freq)));
    expect(exitFund).toEqual(PAPER_EXIT_FUNDAMENTALS);
    // Pine tones are different from paper entry
    expect(buyPlan().map((t) => t.freq)).not.toEqual(entryTri.slice(0, 2));
    expect(sellPlan()[0]!.freq).toBeLessThan(buyPlan()[0]!.freq);
  });

  it("the assist beeps are neither the paper chime nor the paper bell", () => {
    const paper = new Set([...entryPlan(), ...exitPlan()].map((t) => t.freq));
    const entry = assistEntryPlan();
    expect(entry).toHaveLength(3);
    expect(entry.every((t) => t.type === "square" && !paper.has(t.freq))).toBe(true);
    const exit = assistExitPlan();
    expect(exit).toHaveLength(2);
    expect(exit.every((t) => t.type === "square" && !paper.has(t.freq))).toBe(true);
    expect(exit[0]!.freq).toBeLessThan(entry[0]!.freq);
  });
});
