import { describe, expect, it } from "vitest";

import { formatVixChange, vixColor, vixLinePoints, vixShadePoints, vixZone } from "./vix";

const levels = { low: 13, high: 15 };
const colors = { low: "#8b93a7", mid: "#f6c343", high: "#ef5350" };

describe("India VIX helpers", () => {
  it("puts values in zones (low is inclusive for mid, high is strict)", () => {
    expect(vixZone(12.99, levels)).toBe("low");
    expect(vixZone(13, levels)).toBe("mid");
    expect(vixZone(15, levels)).toBe("mid");
    expect(vixZone(15.01, levels)).toBe("high");
    expect(vixColor(16, levels, colors)).toBe("#ef5350");
  });

  it("colours line points and keeps gaps as whitespace", () => {
    expect(vixLinePoints([1, 2, 3], [12, null, 16], levels, colors)).toEqual([
      { time: 1, value: 12, color: "#8b93a7" },
      { time: 2 },
      { time: 3, value: 16, color: "#ef5350" },
    ]);
  });

  it("shades only candles above the high level, and nothing when off", () => {
    const pts = vixShadePoints([1, 2, 3], [14, 15.5, null], 15, "rgba(1,2,3,0.1)", true);
    expect(pts).toEqual([{ time: 1 }, { time: 2, value: 1, color: "rgba(1,2,3,0.1)" }, { time: 3 }]);
    expect(vixShadePoints([1], [20], 15, "x", false)).toEqual([{ time: 1 }]);
  });

  it("formats the change with a sign", () => {
    expect(formatVixChange(0.6234)).toBe("+0.62");
    expect(formatVixChange(-0.3)).toBe("−0.30");
    expect(formatVixChange(0)).toBe("0.00");
    expect(formatVixChange(null)).toBe("–");
  });
});
