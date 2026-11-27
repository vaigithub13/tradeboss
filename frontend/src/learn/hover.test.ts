import { describe, expect, it } from "vitest";

import { hoverPrice, legendBar, legendText } from "./hover";
import { istEpoch, learnAxisTime } from "./time";

const a = { time: istEpoch(2026, 11, 27, 15, 20), open: 25600, high: 25612.5, low: 25590.25, close: 25605.1 };
const b = { time: istEpoch(2026, 11, 27, 15, 25), open: 25605, high: 25610, low: 25580, close: 25593.85 };

describe("Learn hover read-out", () => {
  it("formats the hovered price with two decimals in Indian grouping", () => {
    expect(hoverPrice(25593.85)).toBe("25,593.85");
    expect(hoverPrice(100)).toBe("100.00");
  });

  it("uses the hovered candle and falls back to the latest one", () => {
    expect(legendBar([a, b], a.time)).toBe(a);
    expect(legendBar([a, b], null)).toBe(b);
    expect(legendBar([a, b], 123)).toBe(b);
    expect(legendBar([], null)).toBeNull();
  });

  it("writes time in IST with O/H/L/C and the candle change", () => {
    const text = legendText(b);
    expect(text).toBe(`${learnAxisTime(b.time)} IST  O 25,605.00  H 25,610.00  L 25,580.00  C 25,593.85  −11.15`);
    expect(legendText(a)).toContain("+5.10");
    expect(legendText(null)).toBe("");
  });
});
