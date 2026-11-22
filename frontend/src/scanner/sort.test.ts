import { describe, expect, it } from "vitest";

import { decisionClass, sortRows, verdictClass } from "./sort";

describe("scanner table sort", () => {
  it("sorts numbers and keeps nulls last", () => {
    const rows = [
      { symbol: "B", rr: 1 },
      { symbol: "A", rr: 3 },
      { symbol: "C", rr: null },
    ];
    expect(sortRows(rows, "rr", -1).map((row) => row.symbol)).toEqual(["A", "B", "C"]);
    expect(sortRows(rows, "symbol", 1).map((row) => row.symbol)).toEqual(["A", "B", "C"]);
  });

  it("colours a verdict and a decision", () => {
    expect(verdictClass("GOOD")).toContain("emerald");
    expect(verdictClass("AVOID")).toContain("red");
    expect(decisionClass("BLOCK")).toContain("red");
    expect(decisionClass("PASS")).toContain("emerald");
  });
});
